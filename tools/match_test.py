#!/usr/bin/env python3
"""
match_test.py v2.1: EPG-Titel gegen imdb.db matchen (nur Lesen, schreibt nur Berichte).

Neu in v2.1 (gegenueber v2):
  - "Folge 1 von 10" und "(Teil 2 von 3)" werden bereinigt
  - Suffix-Treffer (Text nach dem Trenner) zaehlen nur noch, wenn es keinen Treffer fuer
    den vollen Titel oder einen Praefix gibt ("Polizeiruf 110: Abgruende" -> Serie, nicht Film "Abgruende")
  - Praefix-Treffer mit mindestens 2 Woertern auf eine Serie bekommen einen Mindestwert
    ("Pfarrer Braun: Adel vernichtet" -> Serie Pfarrer Braun)
  - Abzug bei sehr wenigen IMDb-Stimmen und bei Ein-Wort-Titeln mit wenigen Stimmen
  - Neuer Skip "skip_block": Sendungen ohne Kategorie, >=100 min, >=10x (typisch Radio-Sendestrecken)
  - Retro-Klasse "serie_ende_unbekannt" heisst jetzt "serie_laufend" (IMDb kennt kein Ende)

Ausgabe in --out (Default match_out):
  matches.csv, report.txt, channels_report.csv, channels_ignore.suggested.txt

Nur Standardbibliothek, Python 3.9+.

  ./match_test.py
  ./match_test.py --ignore-channels channels_ignore.txt
"""
import argparse
import csv
import math
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(title: str) -> str:
    """Identisch zu imdb_build.py (Vertrag zwischen EPG-Titeln und IMDb-Namen)."""
    t = title.lower().replace("\u00df", "ss")
    t = t.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " und ")
    return _PUNCT.sub(" ", t).strip()


SKIP_CATEGORY_PREFIX = ("News", "Leisure hobbies : Advertisement", "Sports", "Social / Political",
                        "Education / Science", "Children's / Youth programs : Pre-school")
SKIP_TITLES = {"teleshopping", "sendeschluss", "tagesschau", "heute", "tagesthemen", "wetter",
               "nachrichten", "programmhinweis", "sendepause", "werbung", "infokanal", "infomercial",
               "derzeit keine uebertragung"}
MOVIE_TYPES = {"movie", "tvMovie"}
RETRO_BAD_GENRES = {"News", "Talk-Show", "Game-Show", "Reality-TV", "Documentary", "Music", "Sport", "Short"}
RADIO_NAME_HINT = re.compile(
    r"radio|\bfm\b|welle|antenne|sputnik|jump|\bhit|bayern [1-3]\b|\bswr ?[1-4]\b|\bndr ?[12]\b|"
    r"\bwdr ?[2-5]\b|\bhr ?[1-4]\b|\bb5\b|\bbr ?(klassik|heimat|24)\b|deutschlandfunk|\bdlf\b|\bo3\b|\bfm4\b|cosmo|you ?fm|\bkultur\b|info$",
    re.I)

CLEAN_PATTERNS = [
    re.compile(r"\s*\((?:hd|wh|wdh|ut|ad|s/w|omu|ov)\)\s*$", re.I),
    re.compile(r"\s*\(S\d+\s*/?\s*E\d+\)\s*$", re.I),
    re.compile(r"\s*\(?\s*(?:teil|folge)\s*\d+\s*(?:von|/)\s*\d+\s*\)?\s*$", re.I),
    re.compile(r"\s*\(\d+\s*/\s*\d+\)\s*$"),
    re.compile(r"\s*[-:,]?\s*(?:teil|folge|staffel|episode|ep\.?)\s*\d+(?:\s*/\s*\d+)?\s*$", re.I),
    re.compile(r"\s+\(\d{1,4}\)\s*$"),
    re.compile(r"\s+wiederholung\s*$", re.I),
]
YEAR_IN_TITLE = re.compile(r"\s*\(((?:19[2-9]\d|20[0-3]\d))\)\s*$")
YEAR_IN_DESC = re.compile(r"\b(19[2-9]\d|20[0-2]\d)\b")


def clean_title(t):
    """Gibt (bereinigter_titel, jahr_hinweis, hatte_marker) zurueck."""
    year, changed = None, False
    m = YEAR_IN_TITLE.search(t)
    if m:
        year = int(m.group(1))
        t = t[:m.start()]
    for _ in range(4):
        before = t
        for p in CLEAN_PATTERNS:
            t = p.sub("", t)
        if t == before:
            break
        changed = True
    return t.strip(), year, changed


def candidates(title):
    out = [(title, "full")]
    parts = [p for p in re.split(r"\s+[-\u2013\u2014]\s+|:\s+|\s+:\s+", title) if p.strip()]
    if len(parts) > 1:
        for k in range(len(parts) - 1, 0, -1):
            out.append((" ".join(parts[:k]) if k > 1 else parts[0], f"prefix{k}"))
        out.append((parts[-1], "suffix"))
    seen, res = set(), []
    for text, kind in out:
        n = normalize(text)
        if n and n not in seen:
            seen.add(n)
            res.append((text, n, kind))
    return res


def _split_parts(title):
    prefixes, suffixes = set(), set()
    for _text, norm, kind in candidates(title):
        if kind.startswith("prefix"):
            prefixes.add(norm)
        elif kind == "suffix":
            suffixes.add(norm)
    return prefixes, suffixes


def assign_franchises(shows, min_shows):
    """Erkennt wiederkehrende Praefixe/Suffixe (z. B. Tatort, Alpha Retro)."""
    part_counts = Counter()
    parts = {}
    for key, s in shows.items():
        pref_all, suff_all = _split_parts(s["title"])
        pref = {n for n in pref_all if len(n) >= 3}
        suff = {n for n in suff_all if len(n) >= 3}
        parts[key] = (pref, suff)
        part_counts.update(pref)
        part_counts.update(suff)
    for key, s in shows.items():
        pref, suff = parts[key]
        choices = [(n, "prefix") for n in pref] + [(n, "suffix") for n in suff]
        best = max(choices, key=lambda x: (part_counts[x[0]], len(x[0])), default=None)
        if best is None or part_counts[best[0]] < min_shows:
            continue
        s["franchise"] = {"kind": best[1], "norm": best[0], "count": part_counts[best[0]]}


def read_ignore(path):
    exact, regs = set(), []
    if not path:
        return exact, regs
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("re:"):
            regs.append(re.compile(line[3:], re.I))
        else:
            exact.add(line.lower())
    return exact, regs


def load_shows(epg, ignore):
    exact, regs = ignore
    con = sqlite3.connect(f"file:{epg}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT e.title, e.sub_title, e.description, e.categories, e.start_ts, e.stop_ts,
                  COALESCE(c.name, e.channel_id) AS ch
           FROM events e LEFT JOIN channels c ON c.id = e.channel_id""").fetchall()
    con.close()
    shows = {}
    chan_total = Counter()
    ignored = 0
    for r in rows:
        ch = r["ch"]
        chan_total[ch] += 1
        if ch.lower() in exact or any(rg.search(ch) for rg in regs):
            ignored += 1
            continue
        clean, yhint, marked = clean_title(r["title"])
        key = normalize(clean) or normalize(r["title"])
        s = shows.get(key)
        if s is None:
            s = shows[key] = {"title": clean or r["title"], "raw": Counter(), "n": 0, "durs": [], "chs": Counter(),
                              "cats": Counter(), "subs": set(), "desc": "", "next": None, "yhint": None,
                              "franchise": None}
        s["raw"][r["title"]] += 1
        s["n"] += 1
        s["durs"].append(max(0, (r["stop_ts"] - r["start_ts"]) // 60))
        s["chs"][ch] += 1
        if r["categories"]:
            s["cats"][r["categories"]] += 1
        if r["sub_title"]:
            s["subs"].add(r["sub_title"])
        if marked:
            s["subs"].add(r["title"])
        if yhint:
            s["yhint"] = yhint
        if len(r["description"] or "") > len(s["desc"]):
            s["desc"] = r["description"]
        if s["next"] is None or r["start_ts"] < s["next"]:
            s["next"] = r["start_ts"]
    return shows, chan_total, ignored, len(rows)


def skip_reason(s):
    n = normalize(s["title"])
    if n in SKIP_TITLES:
        return "skip_title"
    med = statistics.median(s["durs"]) if s["durs"] else 0
    if med < 8:
        return "skip_short"
    if not s["cats"] and med >= 100 and s["n"] >= 10:
        return "skip_block"
    if s["cats"]:
        cat = s["cats"].most_common(1)[0][0]
        if any(p in cat for p in SKIP_CATEGORY_PREFIX):
            return "skip_category"
    return None


def lookup(con, norm, limit=40):
    return con.execute(
        """SELECT DISTINCT t.tconst, t.title_type, t.primary_title, t.start_year, t.end_year, t.runtime_min,
                  t.num_votes, t.avg_rating, t.genres
           FROM title_names n JOIN titles t ON t.tconst = n.tconst
           WHERE n.norm = ? ORDER BY t.num_votes DESC LIMIT ?""", (norm, limit)).fetchall()


def score(cand_norm, kind, full_norm, row, dur, n_events, n_subs, desc):
    words_c, words_f = len(cand_norm.split()), max(1, len(full_norm.split()))
    if kind == "full":
        name = 1.0
    elif kind.startswith("prefix"):
        name = 0.55 + 0.35 * (words_c / words_f)
    else:
        name = 0.45
    if len(cand_norm.replace(" ", "")) <= 4:
        name -= 0.2
    adj = 0.0
    ttype, rt = row["title_type"], row["runtime_min"]
    seriesish = n_events >= 4 and n_subs >= 2
    is_movie = ttype in MOVIE_TYPES
    if is_movie:
        if dur >= 75:
            adj += 0.15
        elif dur and dur < 45:
            adj -= 0.35
        elif dur and dur < 60:
            adj -= 0.2
        if dur and dur < 30:
            adj -= 0.2
        if seriesish:
            adj -= 0.15
        if kind.startswith("prefix"):
            adj -= 0.25
        if rt and dur:
            ratio = dur / rt
            adj += 0.1 if 0.9 <= ratio <= 1.7 else (-0.1 if ratio < 0.8 else 0)
    else:
        if dur and dur <= 65:
            adj += 0.15
        elif dur >= 100:
            adj -= 0.1
        if seriesish:
            adj += 0.1
        if kind.startswith("prefix"):
            adj += 0.1
            if words_c >= 2:
                name = max(name, 0.85)
    votes = row["num_votes"] or 0
    adj += min(0.1, math.log10(votes + 1) / 60)
    if votes < 50:
        adj -= 0.15
    if len(full_norm.split()) == 1 and votes < 200:
        adj -= 0.2
    if row["start_year"]:
        years = {int(y) for y in YEAR_IN_DESC.findall(desc or "")}
        if row["start_year"] in years:
            adj += 0.1
    return name + adj, name


def apply_franchise(sc, name, kind, norm, row, s, args):
    franchise = s.get("franchise")
    if not franchise:
        return sc
    is_series = row["title_type"] in ("tvSeries", "tvMiniSeries")
    kind_matches = kind.startswith("prefix") if franchise["kind"] == "prefix" else kind == "suffix"
    is_franchise = kind_matches and norm == franchise["norm"]
    if is_franchise and is_series:
        if name < 0.85:
            sc += 0.85 - name
        sc += args.franchise_bonus
    elif not is_franchise and (kind.startswith("prefix") or kind == "suffix"):
        sc -= args.franchise_mismatch_penalty
    if kind == "suffix" and not (is_franchise and is_series):
        sc -= args.suffix_penalty
    return sc


def match_show(con, s, args):
    title = s["title"]
    full_norm = normalize(title)
    dur = int(statistics.median(s["durs"])) if s["durs"] else 0
    base = []
    for text, norm, kind in candidates(title):
        for row in lookup(con, norm):
            sc, name = score(norm, kind, full_norm, row, dur, s["n"], len(s["subs"]), s["desc"])
            if s["yhint"] and row["start_year"] == s["yhint"]:
                sc += 0.15
            base.append((sc, row, text, kind, norm, name))
    full_series = any(kind == "full" and row["title_type"] in ("tvSeries", "tvMiniSeries")
                      and sc >= args.review for sc, row, _text, kind, _norm, _name in base)
    franchise = s.get("franchise")
    specific_series = False
    if franchise and franchise["kind"] == "prefix":
        specific_series = any(
            kind.startswith("prefix") and row["title_type"] in ("tvSeries", "tvMiniSeries")
            and norm != franchise["norm"] and norm.startswith(franchise["norm"] + " ")
            and sc >= args.review
            for sc, row, _text, kind, norm, _name in base)
    block_franchise = full_series or specific_series
    scored = {}
    for sc, row, text, kind, norm, name in base:
        if (not full_series and kind.startswith("prefix")
                and row["title_type"] in ("tvSeries", "tvMiniSeries")):
            sc += 0.1 * len(norm.split()) / max(1, len(full_norm.split()))
        if not block_franchise:
            sc = apply_franchise(sc, name, kind, norm, row, s, args)
        key = row["tconst"]
        if key not in scored or sc > scored[key][0]:
            scored[key] = (sc, row, text, kind)
    ranked = sorted(scored.values(), key=lambda x: -x[0])
    if not ranked:
        return dict(status="none", score=0.0, best=None, alt=None, cand="", kind="", dur=dur)
    best = ranked[0]
    sc = best[0]
    if len(ranked) > 1 and best[0] - ranked[1][0] < 0.05 and ranked[1][1]["tconst"] != best[1]["tconst"]:
        sc -= 0.15
    status = "auto" if sc >= args.auto else "review" if sc >= args.review else "none"
    return dict(status=status, score=sc, best=best[1], alt=ranked[1][1] if len(ranked) > 1 else None,
                cand=best[2], kind=best[3], dur=dur)


def retro_class(b, args):
    if not b or not b["start_year"]:
        return ""
    y = b["start_year"]
    genres = set((b["genres"] or "").split(","))
    if genres & RETRO_BAD_GENRES:
        return ""
    if not (args.retro_from <= y <= args.retro_to):
        return ""
    if b["title_type"] in ("movie", "tvMovie"):
        return "film"
    if b["end_year"] and b["end_year"] <= args.retro_end_max:
        return "serie_abgeschlossen"
    if b["end_year"]:
        return "serie_lief_laenger"
    return "serie_laufend"


def main():
    ap = argparse.ArgumentParser(description="EPG-Titel gegen imdb.db matchen (Test v2.1)")
    ap.add_argument("--epg", default="data/epg.db")
    ap.add_argument("--imdb", default="data/imdb.db")
    ap.add_argument("--out", default="match_out")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--ignore-channels", help="Textdatei mit Sendernamen (je Zeile, 're:' fuer Regex, # Kommentar)")
    ap.add_argument("--auto", type=float, default=1.0, help="Schwelle fuer auto (Score, Default 1.0)")
    ap.add_argument("--review", type=float, default=0.6, help="Schwelle fuer review (Default 0.6)")
    ap.add_argument("--no-franchise-auto", action="store_true",
                    help="Serien-Franchises (Tatort, Alpha Retro, ...) nicht bevorzugen")
    ap.add_argument("--franchise-min-shows", type=int, default=3)
    ap.add_argument("--franchise-bonus", type=float, default=0.15)
    ap.add_argument("--franchise-mismatch-penalty", type=float, default=0.15)
    ap.add_argument("--suffix-penalty", type=float, default=0.15)
    ap.add_argument("--retro-from", type=int, default=1980)
    ap.add_argument("--retro-to", type=int, default=1999)
    ap.add_argument("--retro-end-max", type=int, default=2009, help="Serie gilt als abgeschlossen-retro bis end_year")
    args = ap.parse_args()
    args.franchise_auto = not args.no_franchise_auto

    t0 = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shows, chan_total, ignored, n_events_all = load_shows(args.epg, read_ignore(args.ignore_channels))
    if args.franchise_auto:
        assign_franchises(shows, args.franchise_min_shows)
    imdb = sqlite3.connect(f"file:{args.imdb}?mode=ro", uri=True)
    imdb.row_factory = sqlite3.Row
    print(f"{n_events_all} Sendungen, {ignored} von ignorierten Sendern, {len(shows)} bereinigte Titel, matche ...", file=sys.stderr)

    results = []
    for i, (key, s) in enumerate(sorted(shows.items(), key=lambda kv: -kv[1]["n"])):
        reason = skip_reason(s)
        if reason:
            results.append((s, dict(status=reason, score=0.0, best=None, alt=None, cand="", kind="", dur=0)))
        else:
            results.append((s, match_show(imdb, s, args)))
        if (i + 1) % 2000 == 0:
            print(f"  {i + 1}/{len(shows)}", file=sys.stderr)

    def row_of(s, m):
        b, a = m["best"], m["alt"]
        rc = retro_class(b, args)
        running = ""
        if b and b["title_type"] in ("tvSeries", "tvMiniSeries"):
            running = "laufend_oder_unbekannt" if not b["end_year"] else "beendet"
        return {
            "title": s["title"], "events": s["n"], "episodes_seen": len(s["subs"]),
            "channels": "|".join(c for c, _ in s["chs"].most_common(3)),
            "next": datetime.fromtimestamp(s["next"]).strftime("%Y-%m-%d %H:%M") if s["next"] else "",
            "dur_min": m["dur"], "category": (s["cats"].most_common(1)[0][0] if s["cats"] else ""),
            "status": m["status"], "score": f"{m['score']:.2f}", "conf": f"{min(1.0, m['score'] / 1.3):.2f}",
            "tconst": b["tconst"] if b else "", "match": b["primary_title"] if b else "",
            "year": b["start_year"] if b else "", "end_year": (b["end_year"] or "") if b else "",
            "type": b["title_type"] if b else "", "running": running, "retro": rc,
            "votes": b["num_votes"] if b else "", "genres": b["genres"] if b else "",
            "used": f"{m['kind']}:{m['cand']}" if b else "",
            "alt": f"{a['primary_title']} ({a['start_year']}, {a['title_type']})" if a else "",
        }

    rows = [row_of(s, m) for s, m in results]
    with open(out / "matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    ch_stat = defaultdict(lambda: Counter())
    for s, m in results:
        for ch, n in s["chs"].items():
            ch_stat[ch]["events"] += n
            ch_stat[ch][m["status"]] += n
    lines = []
    with open(out / "channels_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "events_total", "events_considered", "matched_share", "skip_share", "name_hint_radio"])
        for ch, tot in chan_total.most_common():
            c = ch_stat.get(ch, Counter())
            ev = c["events"]
            matched = (c["auto"] + c["review"]) / ev if ev else 0
            skipped = sum(v for k, v in c.items() if k.startswith("skip")) / ev if ev else 0
            hint = bool(RADIO_NAME_HINT.search(ch))
            w.writerow([ch, tot, ev, f"{matched:.2f}", f"{skipped:.2f}", int(hint)])
            if ev >= 50 and (matched < 0.02 or (hint and matched < 0.35)):
                lines.append(f"{ch}  # {ev} Sendungen, {matched:.0%} zugeordnet" + (", Name wie Radio" if hint else ""))
    (out / "channels_ignore.suggested.txt").write_text(
        "# Vorschlag. Pruefen, unerwuenschte Zeilen loeschen, an channels_ignore.txt anhaengen.\n"
        + "\n".join(lines) + "\n", encoding="utf-8")

    by = defaultdict(list)
    for r in rows:
        by[r["status"]].append(r)
    total_ev = sum(r["events"] for r in rows)
    L = [f"Match-Test v2.1 {datetime.now():%Y-%m-%d %H:%M}, Laufzeit {time.time() - t0:.0f}s",
         f"Sendungen gesamt: {n_events_all}, von ignorierten Sendern: {ignored}, betrachtet: {total_ev}, Titel: {len(rows)}",
         f"Schwellen: auto>={args.auto}, review>={args.review}; Retro {args.retro_from}-{args.retro_to}, Serienende bis {args.retro_end_max}\n",
         "Status         | Titel  | Sendungen"]
    for st in ["auto", "review", "none", "skip_category", "skip_title", "skip_short", "skip_block"]:
        l = by.get(st, [])
        L.append(f"{st:14} | {len(l):6} | {sum(r['events'] for r in l):6}")
    films = [r for r in rows if r["dur_min"] >= 75 and not r["status"].startswith("skip")]
    fc = Counter(r["status"] for r in films)
    L.append(f"\nLange Sendungen (>=75 min, ohne Skip): {len(films)} Titel, davon auto {fc['auto']}, review {fc['review']}, none {fc['none']}")
    rc = Counter(r["retro"] for r in rows if r["retro"] and r["status"] in ("auto", "review"))
    L.append("Retro-Klassen (auto+review): " + ", ".join(f"{k}:{v}" for k, v in rc.items()))
    L.append(f"Suggested ignore: {len(lines)} Sender (siehe channels_ignore.suggested.txt)\n")

    def block(name, lst, n):
        L.append(f"--- {name} (Top {n} nach Sendungen) ---")
        for r in lst[:n]:
            L.append(f"{r['events']:3}x | {r['title'][:44]:44} | {r['score']:>5} | {r['match'][:28]} ({r['year']}-{r['end_year']},{r['type']}) | {r['dur_min']}min | {r['channels'][:28]} | {r['used'][:36]}")
        L.append("")

    n = args.sample
    block("AUTO", by.get("auto", []), n)
    block("REVIEW", by.get("review", []), n)
    nn = [r for r in by.get("none", []) if r["category"] == "" or "Movie" in r["category"] or "Show" in r["category"]]
    block("NONE (evtl. Filme/Serien)", nn, n)
    for cls in ["serie_abgeschlossen", "film", "serie_lief_laenger", "serie_laufend"]:
        lst = sorted([r for r in rows if r["retro"] == cls and r["status"] in ("auto", "review")], key=lambda r: -r["events"])
        L.append(f"--- RETRO {cls} ({len(lst)} Titel, Top {n}) ---")
        for r in lst[:n]:
            L.append(f"{r['events']:3}x | {r['match'][:34]:34} ({r['year']}-{r['end_year']},{r['type']}) | {r['status']} {r['score']} | {r['channels'][:34]} | next {r['next']} | EPG: {r['title'][:30]}")
        L.append("")
    (out / "report.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:15]))
    print(f"\nDateien in {out}/: matches.csv, report.txt, channels_report.csv, channels_ignore.suggested.txt")


if __name__ == "__main__":
    main()
