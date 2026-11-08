"""Matching der EPG-Shows gegen IMDb (aus tools/match_test.py v2.1).

Enthaelt keine CLI-Logik. `run_match` liest `epg.db`/`imdb.db` (per ATTACH) und
schreibt das Ergebnis nach `app.db` (epg_shows, show_match, event_show).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .cleaning import YEAR_IN_DESC, candidates, clean_title, title_parts
from .config import Config
from .normalize import normalize

MOVIE_TYPES = {"movie", "tvMovie"}
RETRO_BAD_GENRES = {"News", "Talk-Show", "Game-Show", "Reality-TV", "Documentary", "Music", "Sport", "Short"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_ignore(path: str | Path | None) -> tuple[set[str], list[re.Pattern]]:
    """Liest die Ignorierliste. Zeilen mit 're:' sind Regex, '#' ist Kommentar."""
    exact, regs = set(), []
    if not path:
        return exact, regs
    p = Path(path)
    if not p.exists():
        return exact, regs
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("re:"):
            regs.append(re.compile(line[3:], re.I))
        else:
            exact.add(line.lower())
    return exact, regs


_SHOWS_CACHE: dict = {}


def _split_parts(title: str) -> tuple[set[str], set[str]]:
    prefixes, suffixes = set(), set()
    for _text, norm, kind in candidates(title):
        if kind.startswith("prefix"):
            prefixes.add(norm)
        elif kind == "suffix":
            suffixes.add(norm)
    return prefixes, suffixes


def _assign_franchises(shows: dict[str, dict], cfg: Config) -> None:
    """Erkennt wiederkehrende Praefixe/Suffixe ueber alle Shows.

    Beispiel: ``Tatort - ...`` kommt oft vor -> Praefix ``tatort`` ist die
    Franchise. Bei ``Schneewittchen - Der Usedom-Krimi`` kommt der Suffix
    haeufiger vor und gewinnt.
    """
    part_counts: Counter = Counter()
    parts: dict[str, tuple[set[str], set[str]]] = {}
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
        if best is None or part_counts[best[0]] < cfg.franchise_min_shows:
            continue
        s["franchise"] = {"kind": best[1], "norm": best[0], "count": part_counts[best[0]]}


def load_shows(con: sqlite3.Connection, cfg: Config) -> tuple[dict, Counter, int, int]:
    """Gruppiert EPG-Sendungen zu Shows. Gibt (shows, chan_total, ignoriert, gesamt) zurueck.

    Das Ergebnis wird pro Prozess gecacht (Schluessel: Pfad und mtime der epg.db)."""
    def _mt(p):
        return p.stat().st_mtime_ns if p.exists() else None
    cache_key = (str(cfg.epg_db), _mt(cfg.epg_db), _mt(cfg.ignore_channels_file),
                 cfg.franchise_auto, cfg.franchise_min_shows)
    if _SHOWS_CACHE.get("key") == cache_key:
        return _SHOWS_CACHE["val"]
    exact, regs = read_ignore(cfg.ignore_channels_file)
    rows = con.execute(
        """SELECT e.title, e.sub_title, e.description, e.categories, e.start_ts, e.stop_ts,
                  e.channel_id, COALESCE(c.name, e.channel_id) AS ch
           FROM epg.events e LEFT JOIN epg.channels c ON c.id = e.channel_id""").fetchall()
    shows: dict[str, dict] = {}
    chan_total: Counter = Counter()
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
            s = shows[key] = {"key": key, "title": clean or r["title"], "raw": Counter(), "n": 0,
                              "durs": [], "chs": Counter(), "cats": Counter(), "subs": set(),
                              "desc": "", "next": None, "yhint": None, "events": [],
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
        s["events"].append((r["channel_id"], r["start_ts"], r["sub_title"]))

    if cfg.franchise_auto:
        _assign_franchises(shows, cfg)

    result = (shows, chan_total, ignored, len(rows))
    _SHOWS_CACHE["key"] = cache_key
    _SHOWS_CACHE["val"] = result
    return result


def skip_reason(s: dict, cfg: Config) -> str | None:
    """Vorfilter. Gibt den Skip-Grund oder None zurueck."""
    n = normalize(s["title"])
    if n in {normalize(t) for t in cfg.skip_titles}:
        return "skip_title"
    med = statistics.median(s["durs"]) if s["durs"] else 0
    if med < cfg.skip_min_duration:
        return "skip_short"
    if not s["cats"] and med >= cfg.skip_block_min_duration and s["n"] >= cfg.skip_block_min_events:
        return "skip_block"
    if s["cats"]:
        cat = s["cats"].most_common(1)[0][0]
        if any(p in cat for p in cfg.skip_categories):
            return "skip_category"
    return None


def lookup(con: sqlite3.Connection, norm: str, limit: int = 40):
    return con.execute(
        """SELECT DISTINCT t.tconst, t.title_type, t.primary_title, t.original_title,
                  t.start_year, t.end_year, t.runtime_min, t.num_votes, t.avg_rating, t.genres
           FROM imdb.title_names n JOIN imdb.titles t ON t.tconst = n.tconst
           WHERE n.norm = ? ORDER BY t.num_votes DESC LIMIT ?""", (norm, limit)).fetchall()


def _runtime_fit(dur: int, runtime: int | None) -> bool:
    return bool(runtime and dur and 0.9 <= dur / runtime <= 1.7)


def _is_original_or_primary(norm: str, row) -> bool:
    if norm == normalize(row["primary_title"]):
        return True
    original = row["original_title"]
    return bool(original and norm == normalize(original))


def _reconstructed_tconsts(con: sqlite3.Connection, title: str) -> set[str]:
    """tconsts, deren IMDb-Namen alle Teile des EPG-Titels exakt abdecken.

    Beispiel: ``Das Weihnachtskarussell - Alles dreht sich um die Liebe`` ist
    kein einzelner IMDb-Name, aber beide Teile sind deutsche Aliase von
    ``tt13273744``. Der Volltitel selbst darf dafuer kein Treffer sein.
    """
    parts: list[str] = []
    for part in title_parts(title):
        norm = normalize(part)
        if len(norm) >= 3 and norm not in parts:
            parts.append(norm)
    if len(parts) < 2:
        return set()
    matches: list[set[str]] = []
    for norm in parts:
        tconsts = {row["tconst"] for row in lookup(con, norm)}
        if not tconsts:
            return set()
        matches.append(tconsts)
    common = set.intersection(*matches)
    if not common:
        return set()
    full_norm = normalize(title)
    full = {row["tconst"] for row in lookup(con, full_norm)} if full_norm else set()
    return common - full


def score(cand_norm: str, kind: str, full_norm: str, row, dur: int, n_events: int,
          n_subs: int, desc: str | None) -> tuple[float, dict]:
    """Score und die Einzelsignale. Summe der Signale == Total (bis auf Fliesskomma)."""
    signals: dict[str, float] = {}
    words_c, words_f = len(cand_norm.split()), max(1, len(full_norm.split()))
    if kind == "full":
        name = 1.0
    elif kind.startswith("prefix"):
        name = 0.55 + 0.35 * (words_c / words_f)
    else:
        name = 0.45
    if len(cand_norm.replace(" ", "")) <= 4:
        name -= 0.2
        signals["short_name"] = -0.2
    adj = 0.0

    def add(key: str, val: float) -> None:
        nonlocal adj
        adj += val
        signals[key] = round(val, 6)

    ttype, rt = row["title_type"], row["runtime_min"]
    seriesish = n_events >= 4 and n_subs >= 2
    is_movie = ttype in MOVIE_TYPES
    if is_movie:
        if dur >= 75:
            add("movie_long", 0.15)
        elif dur and dur < 45:
            add("movie_too_short", -0.35)
        elif dur and dur < 60:
            add("movie_short", -0.2)
        if dur and dur < 30:
            add("movie_very_short", -0.2)
        if seriesish:
            add("movie_but_seriesish", -0.15)
        if kind.startswith("prefix"):
            add("movie_prefix", -0.25)
        if rt and dur:
            ratio = dur / rt
            add("runtime_ratio", 0.1 if 0.9 <= ratio <= 1.7 else (-0.1 if ratio < 0.8 else 0.0))
    else:
        if dur and dur <= 65:
            add("series_duration", 0.15)
        elif dur >= 100:
            add("series_too_long", -0.1)
        if seriesish:
            add("series_seriesish", 0.1)
        if kind.startswith("prefix"):
            add("series_prefix", 0.1)
            if words_c >= 2:
                name = max(name, 0.85)
    votes = row["num_votes"] or 0
    add("votes", round(min(0.1, math.log10(votes + 1) / 60), 6))
    if votes < 50:
        add("low_votes", -0.15)
    if len(full_norm.split()) == 1 and votes < 200:
        add("one_word_low_votes", -0.2)
    signals["name"] = round(name, 6)
    if row["start_year"]:
        years = {int(y) for y in YEAR_IN_DESC.findall(desc or "")}
        if row["start_year"] in years:
            add("year_in_desc", 0.1)
    return name + adj, signals


def _apply_franchise(sc: float, signals: dict, kind: str, norm: str, row,
                     s: dict, cfg: Config) -> tuple[float, dict]:
    """Passt einen Kandidaten an die erkannte Serien-Franchise an."""
    franchise = s.get("franchise")
    if not franchise:
        return sc, signals
    is_series = row["title_type"] in ("tvSeries", "tvMiniSeries")
    kind_matches = kind.startswith("prefix") if franchise["kind"] == "prefix" else kind == "suffix"
    is_franchise = kind_matches and norm == franchise["norm"]
    if is_franchise and is_series:
        name = signals.get("name", 0.0)
        if name < 0.85:
            boost = 0.85 - name
            sc += boost
            signals["franchise_name"] = round(boost, 6)
            signals["name"] = 0.85
        sc += cfg.franchise_bonus
        signals["franchise_bonus"] = cfg.franchise_bonus
    elif not is_franchise and (kind.startswith("prefix") or kind == "suffix"):
        sc -= cfg.franchise_mismatch_penalty
        signals["franchise_mismatch"] = -cfg.franchise_mismatch_penalty
    if kind == "suffix" and not (is_franchise and is_series):
        sc -= cfg.suffix_penalty
        signals["suffix_penalty"] = -cfg.suffix_penalty
    return sc, signals


def rank_candidates(con: sqlite3.Connection, s: dict, cfg: Config) -> list[tuple]:
    """Alle Kandidaten eines Shows, nach Score absteigend. Tupel:
    (score, row, text, kind, signals, year_title_bonus)."""
    title = s["title"]
    full_norm = normalize(title)
    dur = int(statistics.median(s["durs"])) if s["durs"] else 0
    reconstructed = _reconstructed_tconsts(con, title)
    parts = [normalize(part) for part in title_parts(title) if normalize(part)]
    base: list[tuple] = []
    for text, norm, kind in candidates(title):
        for row in lookup(con, norm):
            sc, signals = score(norm, kind, full_norm, row, dur, s["n"], len(s["subs"]), s["desc"])
            if row["tconst"] in reconstructed:
                sc += cfg.combined_name_bonus
                signals["combined_name"] = cfg.combined_name_bonus
            bonus = 0.0
            if s["yhint"] and row["start_year"] == s["yhint"]:
                bonus = 0.15
                sc += bonus
            base.append((sc, row, text, kind, signals, bonus, norm))
    single_candidate = len({item[1]["tconst"] for item in base}) == 1
    rest_unknown: bool | None = None
    adjusted: list[tuple] = []
    for sc, row, text, kind, signals, bonus, norm in base:
        if row["title_type"] in MOVIE_TYPES and _runtime_fit(dur, row["runtime_min"]):
            if kind == "prefix1" and len(parts) > 1 and _is_original_or_primary(norm, row):
                if rest_unknown is None:
                    rest_unknown = all(not lookup(con, part) for part in parts[1:])
                if rest_unknown:
                    sc += cfg.original_prefix_bonus
                    signals["original_prefix"] = cfg.original_prefix_bonus
            if kind == "full" and single_candidate and _is_original_or_primary(norm, row):
                sc += cfg.single_exact_bonus
                signals["single_exact"] = cfg.single_exact_bonus
        adjusted.append((sc, row, text, kind, signals, bonus, norm))
    base = adjusted
    # Ein exakter Volltreffer auf eine Serie ist staerker als ein Serien-Praefix
    # (z. B. "In aller Freundschaft - Die jungen Aerzte" ist ein eigenes Spin-off).
    full_series = any(kind == "full" and row["title_type"] in ("tvSeries", "tvMiniSeries")
                      and sc >= cfg.review_threshold
                      for sc, row, _text, kind, _signals, _bonus, _norm in base)
    franchise = s.get("franchise")
    specific_series = False
    if franchise and franchise["kind"] == "prefix":
        specific_series = any(
            kind.startswith("prefix") and row["title_type"] in ("tvSeries", "tvMiniSeries")
            and norm != franchise["norm"] and norm.startswith(franchise["norm"] + " ")
            and sc >= cfg.review_threshold
            for sc, row, _text, kind, _signals, _bonus, norm in base)
    block_franchise = full_series or specific_series
    scored: dict[str, tuple] = {}
    for sc, row, text, kind, signals, bonus, norm in base:
        if (not full_series and kind.startswith("prefix")
                and row["title_type"] in ("tvSeries", "tvMiniSeries")):
            words_c = len(norm.split())
            words_f = max(1, len(full_norm.split()))
            spec = round(0.1 * words_c / words_f, 6)
            sc += spec
            signals["series_prefix_specific"] = spec
        if not block_franchise:
            sc, signals = _apply_franchise(sc, signals, kind, norm, row, s, cfg)
        key = row["tconst"]
        if key not in scored or sc > scored[key][0]:
            scored[key] = (sc, row, text, kind, signals, bonus)
    return sorted(scored.values(), key=lambda x: -x[0])


def _ambiguity_penalty(ranked: list[tuple], idx: int = 0) -> float:
    """Abzug nur fuer den besten Kandidaten, wenn der zweite sehr nah liegt."""
    if idx != 0 or len(ranked) < 2:
        return 0.0
    best, second = ranked[0], ranked[1]
    if best[0] - second[0] < 0.05 and best[1]["tconst"] != second[1]["tconst"]:
        return -0.15
    return 0.0


def _status_for(score_value: float, cfg: Config) -> str:
    if score_value >= cfg.auto_threshold:
        return "auto"
    if score_value >= cfg.review_threshold:
        return "review"
    return "none"


def finalize_ranked(ranked: list[tuple], cfg: Config) -> list[dict]:
    """Wendet Ambiguitaets-Abzug und Status auf eine Rangliste an.

    Die internen Schluessel (``_row``, ``_raw_score``, ``_final_score``,
    ``_bonus``) werden von ``candidate_details`` entfernt.
    """
    out = []
    for idx, (sc, row, text, kind, signals, bonus) in enumerate(ranked):
        penalty = _ambiguity_penalty(ranked, idx)
        final_score = sc + penalty
        out.append({
            "_row": row, "_raw_score": sc, "_final_score": final_score, "_bonus": bonus,
            "tconst": row["tconst"], "title": row["primary_title"], "year": row["start_year"],
            "end_year": row["end_year"], "type": row["title_type"], "genres": row["genres"],
            "votes": row["num_votes"], "score": round(final_score, 3), "conf": round(conf(final_score), 2),
            "kind": kind, "status": _status_for(final_score, cfg), "signals": signals,
            "ambiguity_penalty": penalty, "candidate": text,
        })
    return out


def candidate_details(con: sqlite3.Connection, s: dict, cfg: Config, limit: int | None = None) -> list[dict]:
    """Vollstaendige Kandidatenliste inkl. Signale, fuer Diagnose/Support-API."""
    items = finalize_ranked(rank_candidates(con, s, cfg), cfg)
    if limit is not None:
        items = items[:limit]
    return [{
        "tconst": item["tconst"], "title": item["title"], "year": item["year"],
        "end_year": item["end_year"], "type": item["type"], "genres": item["genres"],
        "votes": item["votes"], "score": item["score"], "raw_score": round(item["_raw_score"], 3),
        "conf": item["conf"], "kind": item["kind"], "status": item["status"],
        "signals": item["signals"], "year_title_bonus": item["_bonus"],
        "ambiguity_penalty": item["ambiguity_penalty"], "candidate": item["candidate"],
    } for item in items]


def suggestions(con: sqlite3.Connection, s: dict, cfg: Config, limit: int = 5) -> list[dict]:
    """Top-Kandidaten mit Status und Konfidenz, fuer die Review-Ansicht."""
    return [{
        "tconst": item["tconst"], "title": item["title"], "year": item["year"],
        "end_year": item["end_year"], "type": item["type"], "genres": item["genres"],
        "votes": item["votes"], "score": item["score"], "conf": item["conf"],
        "kind": item["kind"], "status": item["status"],
    } for item in finalize_ranked(rank_candidates(con, s, cfg), cfg)[:limit]]


def search_titles(con: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Sucht IMDb-Titel fuer 'anderer Titel waehlen'.

    Prefix-Bereich auf `title_names.norm` (nutzt den Index) statt teurem LIKE '%...%'.
    """
    q = normalize(query)
    if not q:
        return []
    rows = con.execute(
        """SELECT t.tconst, t.title_type, t.primary_title, t.start_year, t.end_year,
                  t.genres, t.num_votes, min(n.norm) AS matched
           FROM imdb.title_names n JOIN imdb.titles t ON t.tconst = n.tconst
           WHERE n.norm >= ? AND n.norm < ?
           GROUP BY t.tconst ORDER BY t.num_votes DESC LIMIT ?""", (q, q + "\uffff", limit)).fetchall()
    return [{"tconst": r["tconst"], "title": r["primary_title"], "year": r["start_year"],
             "end_year": r["end_year"], "type": r["title_type"], "genres": r["genres"],
             "votes": r["num_votes"]} for r in rows]


def match_show(con: sqlite3.Connection, s: dict, cfg: Config) -> dict:
    ranked = rank_candidates(con, s, cfg)
    dur = int(statistics.median(s["durs"])) if s["durs"] else 0
    if not ranked:
        return dict(status="none", score=0.0, best=None, alt=None, cand="", kind="", dur=dur,
                    reason={"candidate": None, "kind": None, "signals": {}, "year_title_bonus": 0.0,
                            "ambiguity_penalty": 0.0, "final_score": 0.0})
    final = finalize_ranked(ranked, cfg)[0]
    sc = final["_final_score"]
    reason = {
        "candidate": final["candidate"],
        "kind": final["kind"],
        "signals": final["signals"],
        "year_title_bonus": final["_bonus"],
        "ambiguity_penalty": final["ambiguity_penalty"],
        "final_score": round(sc, 6),
    }
    return dict(status=final["status"], score=sc, best=ranked[0][1],
                alt=ranked[1][1] if len(ranked) > 1 else None,
                cand=final["candidate"], kind=final["kind"], dur=dur, reason=reason)


def conf(score_value: float) -> float:
    return min(1.0, score_value / 1.3)


def retro_class(b, cfg: Config) -> str:
    """Retro-Klasse nach SPEC 5.3 (leer, wenn nicht passend)."""
    if not b or not b["start_year"]:
        return ""
    y = b["start_year"]
    genres = set((b["genres"] or "").split(","))
    if genres & RETRO_BAD_GENRES:
        return ""
    if not (cfg.retro_from <= y <= cfg.retro_to):
        return ""
    if b["title_type"] in ("movie", "tvMovie"):
        return "film"
    if b["end_year"] and b["end_year"] <= cfg.retro_end_max:
        return "serie_abgeschlossen"
    if b["end_year"]:
        return "serie_lief_laenger"
    return "serie_laufend"


def match_shows(con: sqlite3.Connection, cfg: Config, progress=None) -> tuple[list[tuple[dict, dict]], dict]:
    """Berechnet fuer alle Shows das Matching. Gibt ([(show, result)], stats) zurueck."""
    shows, chan_total, ignored, n_events_all = load_shows(con, cfg)
    results: list[tuple[dict, dict]] = []
    ordered = sorted(shows.items(), key=lambda kv: -kv[1]["n"])
    for i, (_key, s) in enumerate(ordered):
        reason = skip_reason(s, cfg)
        if reason:
            res = dict(status=reason, score=0.0, best=None, alt=None, cand="", kind="", dur=0, reason=None)
        else:
            res = match_show(con, s, cfg)
        results.append((s, res))
        if progress and (i + 1) % 2000 == 0:
            progress(i + 1, len(shows))
    stats = {
        "events_total": n_events_all,
        "events_considered": n_events_all - ignored,
        "events_ignored": ignored,
        "shows": len(shows),
        "by_status": Counter(res["status"] for _s, res in results),
    }
    return results, stats


def _load_overrides(con: sqlite3.Connection) -> dict[str, str | None]:
    return {r["show_key"]: r["tconst"] for r in con.execute("SELECT show_key, tconst FROM overrides")}


def persist(con: sqlite3.Connection, results: list[tuple[dict, dict]], overrides: dict[str, str | None]) -> None:
    """Schreibt epg_shows, show_match und event_show. Skips werden nicht gespeichert.

    Nicht mehr vorhandene Shows (z. B. durch neu ignorierte Sender) werden entfernt.
    """
    now = _now()
    con.execute("DELETE FROM event_show")
    kept: list[int] = []
    for s, res in results:
        status = res["status"]
        if status.startswith("skip"):
            continue
        con.execute(
            """INSERT INTO epg_shows (show_key, show_name, first_seen) VALUES (?,?,?)
               ON CONFLICT(show_key) DO UPDATE SET show_name = excluded.show_name""",
            (s["key"], s["title"], now))
        show_id = con.execute("SELECT id FROM epg_shows WHERE show_key = ?", (s["key"],)).fetchone()[0]
        kept.append(show_id)
        con.executemany(
            "INSERT OR IGNORE INTO event_show (channel_id, start_ts, show_id, episode_hint) VALUES (?,?,?,?)",
            [(ch, ts, show_id, hint) for ch, ts, hint in s["events"]])

        override = overrides.get(s["key"], "__none__")
        existing = con.execute("SELECT status FROM show_match WHERE show_id = ?", (show_id,)).fetchone()
        if override != "__none__":
            final_status = "confirmed" if override else "rejected"
            tconst, method, sc, reason = override, "override", None, None
        elif existing is not None and existing["status"] in ("confirmed", "rejected"):
            continue
        else:
            final_status = status
            tconst = res["best"]["tconst"] if res["best"] else None
            method = res["kind"] or None
            sc = res["score"]
            reason = json.dumps(res["reason"], ensure_ascii=False) if res["reason"] else None
        con.execute(
            """INSERT INTO show_match (show_id, tconst, status, score, method, reason, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(show_id) DO UPDATE SET tconst=excluded.tconst, status=excluded.status,
                 score=excluded.score, method=excluded.method, reason=excluded.reason,
                 updated_at=excluded.updated_at""",
            (show_id, tconst, final_status, sc, method, reason, now))

    con.execute("CREATE TEMP TABLE IF NOT EXISTS _kept_show (id INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM _kept_show")
    con.executemany("INSERT INTO _kept_show (id) VALUES (?)", [(show_id,) for show_id in kept])
    con.execute("DELETE FROM show_match WHERE show_id NOT IN (SELECT id FROM _kept_show)")
    con.execute("DELETE FROM epg_shows WHERE id NOT IN (SELECT id FROM _kept_show)")
    con.commit()


def run_match(con: sqlite3.Connection, cfg: Config, progress=None) -> dict:
    """Vollstaendiger Match-Lauf inkl. Persistenz. Gibt die Statistik zurueck."""
    started = datetime.now(timezone.utc)
    results, stats = match_shows(con, cfg, progress=progress)
    persist(con, results, _load_overrides(con))
    stats["seconds"] = (datetime.now(timezone.utc) - started).total_seconds()
    return stats
