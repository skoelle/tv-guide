"""FastAPI-App: Jetzt, Stöbern, Werk-Detail, Status, JSON-API, Healthcheck."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from .. import __version__
from ..browse import (airing_summary, annotate_next, apply_filters, catalog_facets, evening_airings,
                      find_work, genre_de, imdb_source_date, imdb_type_de, load_catalog,
                      parse_filters, running_airings, sort_works)
from ..categories import label as category_label
from ..marks import delete_mark, get_marks, recent as recent_marks, set_mark, undo_last as undo_last_mark
from ..config import Config, load_config
from ..db import Db, connect
from ..jobs import get_job, recent_jobs, recover_stale, running_job, start_job
from ..matching import search_titles
from ..posters import TCONST_RE, cached_find_summary
from ..review import (apply_override, list_review, mark_as_format, reopen_work, review_count,
                      review_entries_for_tconst, show_key_for, undo_last)
from ..scheduler import Scheduler
from ..search import (create_search, delete_search, get_search, hits_for, list_searches, run_searches,
                      update_search)
from ..status import collect, fmt_num, fmt_size, fmt_ts
from ..tvheadend import TvheadendClient, TvheadendError, api_base

HERE = Path(__file__).parent


class SearchCreate(BaseModel):
    name: str = "Suche"
    filter: dict = {}
    include_review: bool = False
    notify: bool = False


class SearchUpdate(BaseModel):
    name: str | None = None
    include_review: bool | None = None
    notify: bool | None = None


def _qs(f: dict) -> str:
    """Filter als Query-String (nur Abweichungen vom Standard)."""
    parts: list[str] = []
    if f.get("category") and f["category"] != {"film", "serie"}:
        parts.append("category=" + ",".join(sorted(f["category"])))
    if f["type"]:
        parts.append("type=" + ",".join(sorted(f["type"])))
    if f["decade"]:
        parts.append("decade=" + ",".join(str(d) for d in sorted(f["decade"])))
    if f["group"]:
        parts.append("group=" + f["group"])
    if f["genre"]:
        parts.append("genre=" + ",".join(sorted(f["genre"])))
    if f.get("genre_not"):
        parts.append("genre_not=" + ",".join(sorted(f["genre_not"])))
    if f["status"] and f["status"] != {"sicher", "unsicher"}:
        parts.append("status=" + ",".join(sorted(f["status"])))
    if f["retro"]:
        parts.append("retro=1")
    if f.get("upcoming"):
        parts.append("upcoming=1")
    if f["q"]:
        parts.append("q=" + quote(f["q"]))
    if f["sort"] and f["sort"] != "termin":
        parts.append("sort=" + f["sort"])
    if f.get("page", 1) > 1:
        parts.append("page=" + str(f["page"]))
    return "&".join(parts)


def _toggle(f: dict, key: str, value=None) -> dict:
    new = {**f, "page": 1, "type": None if f["type"] is None else set(f["type"]),
           "category": None if f.get("category") is None else set(f["category"]),
           "decade": None if f["decade"] is None else set(f["decade"]),
           "status": None if f["status"] is None else set(f["status"]),
           "genre": None if f["genre"] is None else set(f["genre"]),
           "genre_not": None if f.get("genre_not") is None else set(f["genre_not"])}
    if key in ("genre", "genre_not"):
        other = "genre_not" if key == "genre" else "genre"
        opposite = set(new[other] or set())
        opposite.discard(value)
        new[other] = opposite or None
        cur = set(new[key] or set())
        if value in cur:
            cur.discard(value)
        else:
            cur.add(value)
        new[key] = cur or None
    elif key in ("type", "category", "decade", "status"):
        cur = set(new[key] or set())
        if value in cur:
            cur.discard(value)
        else:
            cur.add(value)
        new[key] = cur or None
    elif key == "group":
        new["group"] = None if f["group"] == value else value
    elif key == "retro":
        new["retro"] = not f["retro"]
    elif key == "upcoming":
        new["upcoming"] = not f.get("upcoming")
    return new


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            startup_con = connect(cfg)
            try:
                recover_stale(startup_con)
            finally:
                startup_con.close()
        except Exception:  # noqa: BLE001 - Start darf daran nicht scheitern
            pass
        scheduler = None
        if cfg.schedule_enabled:
            scheduler = Scheduler(cfg)
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()

    app = FastAPI(title="TV-Guide", version=__version__, lifespan=lifespan)
    app.state.cfg = cfg
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["num"] = fmt_num
    templates.env.filters["size"] = fmt_size
    templates.env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, cfg.timezone, fmt)
    templates.env.globals["genre_de"] = genre_de
    templates.env.globals["imdb_type_de"] = imdb_type_de
    templates.env.globals["category_label"] = category_label
    templates.env.globals["decade_label"] = lambda d: f"{d}er" if d >= 2000 else f"{str(d)[2:]}er"
    templates.env.globals["with_sort"] = lambda f, v: {**f, "sort": v}
    templates.env.globals["with_page"] = lambda f, p: {**f, "page": p}

    def get_db():
        con = connect(cfg)
        try:
            yield con
        finally:
            con.close()

    def _nav(con, active: str) -> dict:
        review = review_count(con)
        searches = con.execute("SELECT count(*) FROM saved_searches").fetchone()[0]
        return {"active": active, "review": review, "searches": searches}

    def _at(request: Request) -> int:
        raw = request.query_params.get("at")
        return int(raw) if raw and raw.isdigit() else int(time.time())

    def _base(request, con, active) -> dict:
        return {"request": request, "version": __version__, "nav": _nav(con, active),
                "qs": _qs, "toggle": _toggle, "tz": cfg.timezone,
                "retro_from": cfg.retro_from, "retro_to": cfg.retro_to}

    @app.get("/")
    def index(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        ctx = _base(request, con, "jetzt")
        ctx.update({
            "f": f, "facets": catalog_facets(works, f, cfg, at), "at": at,
            "running": running_airings(works, at, f, cfg),
            "evening": evening_airings(works, at, f, cfg),
        })
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.get("/browse")
    def browse(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        selected = sort_works(apply_filters(works, f, cfg, at), f, at)
        total = len(selected)
        per_page = 60
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(f["page"], pages)
        f = {**f, "page": page}
        shown = annotate_next(selected[(page - 1) * per_page: page * per_page], at)
        ctx = _base(request, con, "stoebern")
        ctx.update({"f": f, "facets": catalog_facets(works, f, cfg, at), "at": at,
                    "works": shown, "total": total, "page": page, "pages": pages})
        return templates.TemplateResponse(request, "browse.html", ctx)

    @app.get("/work/{tconst}")
    def work_page(request: Request, tconst: str, con: Db = Depends(get_db)):
        works = load_catalog(con, cfg)
        w = find_work(works, tconst)
        if w is None:
            return templates.TemplateResponse(request, "not_found.html",
                                              {**_base(request, con, ""), "tconst": tconst}, status_code=404)
        tz = ZoneInfo(cfg.timezone)
        groups: list[dict] = []
        for a in w["airings"]:
            label = datetime.fromtimestamp(a["start_ts"], tz).strftime("%a, %d.%m.%Y")
            if not groups or groups[-1]["label"] != label:
                groups.append({"label": label, "airings": []})
            groups[-1]["airings"].append(a)
        review_shows = (review_entries_for_tconst(con, cfg, tconst)
                        if w["status"] == "review" else [])
        channels = {a["channel_id"]: a["channel"] for a in w["airings"]}
        tvh_channels = sorted(channels.items(), key=lambda item: item[1].lower())
        tmdb_info = cached_find_summary(con, cfg, tconst, w["type"])
        if tmdb_info is None and w.get("tmdb_url"):
            tmdb_info = {"url": w["tmdb_url"]}
        ctx = _base(request, con, "")
        ctx.update({"w": w, "groups": groups, "epg": airing_summary(w["airings"]),
                    "mark": get_marks(con).get(tconst),
                    "review_shows": review_shows, "imdb_source_date": imdb_source_date(con),
                    "tmdb_info": tmdb_info,
                    "at": _at(request), "tvh_enabled": bool(api_base(cfg)),
                    "tvh_channels": tvh_channels})
        return templates.TemplateResponse(request, "work.html", ctx)

    @app.get("/api/now")
    def api_now(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        return {"at": at,
                "running": [{"tconst": x["work"]["tconst"], "title": x["work"]["title"],
                             "poster": x["work"].get("poster", False), **x["airing"]}
                            for x in running_airings(works, at, f, cfg)],
                "evening": [{"tconst": x["work"]["tconst"], "title": x["work"]["title"],
                             "poster": x["work"].get("poster", False), **x["airing"]}
                            for x in evening_airings(works, at, f, cfg)]}

    @app.get("/api/browse")
    def api_browse(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = sort_works(apply_filters(load_catalog(con, cfg), f, cfg, at), f, at)
        return {"total": len(works),
                "works": [{k: w[k] for k in ("tconst", "title", "year", "end_year", "type", "kind",
                                             "category", "genres", "rating", "votes", "status", "retro",
                                             "poster")}
                          | {"count": len(w["airings"])} for w in works]}

    @app.get("/api/works/{tconst}")
    def api_work(tconst: str, con: Db = Depends(get_db)):
        w = find_work(load_catalog(con, cfg), tconst)
        if w is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return w

    @app.get("/poster/{tconst}")
    def poster_file(tconst: str):
        if not TCONST_RE.fullmatch(tconst):
            return Response(status_code=404)
        path = (cfg.posters_dir / f"{tconst}.jpg").resolve()
        if cfg.posters_dir.resolve() not in path.parents or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=2592000"})

    @app.get("/review")
    def review_page(request: Request, con: Db = Depends(get_db)):
        per_page = 25
        total = review_count(con)
        pages = max(1, (total + per_page - 1) // per_page)
        raw = request.query_params.get("page", "1")
        page = min(max(1, int(raw) if raw.isdigit() else 1), pages)
        shows = list_review(con, cfg, limit=per_page, offset=(page - 1) * per_page)
        ctx = _base(request, con, "review")
        ctx.update({"shows": shows, "total": total, "page": page, "pages": pages,
                    "marks": recent_marks(con)})
        return templates.TemplateResponse(request, "review.html", ctx)

    @app.post("/api/review/format")
    def api_mark_format(show_key: str, con: Db = Depends(get_db)):
        tconst = mark_as_format(con, show_key)
        if tconst is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return {"ok": True, "tconst": tconst}

    @app.post("/api/review/confirm")
    def api_review_confirm(show_key: str = Form(""), tconst: str = Form(""),
                           con: Db = Depends(get_db)):
        key = show_key_for(con, show_key)
        if key is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if not tconst.strip():
            return JSONResponse({"ok": False, "error": "tconst required"}, status_code=400)
        apply_override(con, key, tconst.strip())
        return {"ok": True, "show_key": key, "tconst": tconst.strip()}

    @app.post("/api/review/reject")
    def api_review_reject(show_key: str = Form(""), con: Db = Depends(get_db)):
        key = show_key_for(con, show_key)
        if key is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        apply_override(con, key, None)
        return {"ok": True, "show_key": key, "tconst": None}

    @app.post("/api/review/undo")
    def api_review_undo(con: Db = Depends(get_db)):
        return {"ok": True, "show_key": undo_last(con)}

    @app.post("/api/marks/undo")
    def api_marks_undo(con: Db = Depends(get_db)):
        return {"removed": undo_last_mark(con)}

    @app.post("/api/works/{tconst}/reopen")
    def api_work_reopen(tconst: str, con: Db = Depends(get_db)):
        shows = reopen_work(con, tconst)
        if shows == 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return {"ok": True, "tconst": tconst, "shows": shows, "review": review_count(con)}

    @app.post("/api/works/{tconst}/mark")
    def api_work_mark(tconst: str, mark: str, con: Db = Depends(get_db)):
        if mark == "none":
            delete_mark(con, tconst)
        elif mark in ("format", "keep"):
            set_mark(con, tconst, mark)
        else:
            return JSONResponse({"ok": False, "error": "invalid mark"}, status_code=400)
        return {"ok": True, "tconst": tconst, "mark": mark}

    def _work_airing(con, tconst: str, channel_id: str, start_ts: int | None):
        w = find_work(load_catalog(con, cfg), tconst)
        if w is None or start_ts is None:
            return w, None
        airing = next((a for a in w["airings"]
                       if str(a["channel_id"]) == channel_id and int(a["start_ts"]) == start_ts), None)
        return w, airing

    @app.post("/api/works/{tconst}/record-series")
    def api_work_record_series(tconst: str, channel_id: str = Form(""), start_ts: int | None = Form(None),
                               con: Db = Depends(get_db)):
        w, airing = _work_airing(con, tconst, channel_id, start_ts)
        if w is None:
            return JSONResponse({"ok": False, "error": "Werk nicht gefunden."}, status_code=404)
        if w["kind"] != "serie":
            return JSONResponse({"ok": False, "error": "Serien-Timer nur für Serien."}, status_code=400)
        try:
            if start_ts is None:
                if channel_id and not any(str(a["channel_id"]) == channel_id for a in w["airings"]):
                    return JSONResponse({"ok": False, "error": "Unbekannter Sender."}, status_code=400)
                result = TvheadendClient(cfg).record_series_title(w["title"], channel_id)
            else:
                if not channel_id or airing is None:
                    return JSONResponse({"ok": False, "error": "Termin nicht gefunden."}, status_code=400)
                result = TvheadendClient(cfg).record_series(
                    airing["channel_id"], airing["title"], airing["start_ts"], airing["stop_ts"])
        except TvheadendError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        return {"ok": True, **result}

    @app.post("/api/works/{tconst}/record-event")
    def api_work_record_event(tconst: str, channel_id: str = Form(...), start_ts: int = Form(...),
                              con: Db = Depends(get_db)):
        w, airing = _work_airing(con, tconst, channel_id, start_ts)
        if w is None:
            return JSONResponse({"ok": False, "error": "Werk nicht gefunden."}, status_code=404)
        if airing is None:
            return JSONResponse({"ok": False, "error": "Termin nicht gefunden."}, status_code=400)
        if w["kind"] != "film":
            return JSONResponse({"ok": False, "error": "Einzelaufnahme nur für Filme."}, status_code=400)
        try:
            result = TvheadendClient(cfg).record_event(
                airing["channel_id"], airing["title"], airing["start_ts"], airing["stop_ts"])
        except TvheadendError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
        return {"ok": True, **result}

    @app.post("/review/{show_key}/confirm")
    def review_confirm(show_key: str, tconst: str = Form(...), con: Db = Depends(get_db)):
        key = show_key_for(con, show_key)
        if key and tconst.strip():
            apply_override(con, key, tconst.strip())
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/{show_key}/reject")
    def review_reject(show_key: str, con: Db = Depends(get_db)):
        key = show_key_for(con, show_key)
        if key:
            apply_override(con, key, None)
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/undo")
    def review_undo(con: Db = Depends(get_db)):
        undo_last(con)
        return RedirectResponse("/review", status_code=303)

    @app.get("/review/search")
    def review_search(request: Request, con: Db = Depends(get_db), q: str = "", show_key: str = "",
                      scope: str = ""):
        results = search_titles(con, q, 20) if q.strip() else []
        ctx = {"request": request, "results": results, "show_key": show_key, "q": q, "scope": scope}
        return templates.TemplateResponse(request, "_review_search.html", ctx)

    @app.get("/api/review")
    def api_review(request: Request, con: Db = Depends(get_db)):
        per_page = 50
        raw = request.query_params.get("page", "1")
        page = max(1, int(raw) if raw.isdigit() else 1)
        return {"total": review_count(con),
                "shows": list_review(con, cfg, limit=per_page, offset=(page - 1) * per_page)}

    @app.post("/api/review/{ident}")
    async def api_review_post(ident: str, request: Request, con: Db = Depends(get_db)):
        key = show_key_for(con, ident)
        if key is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await request.json()
        tconst = None if body.get("reject") else (body.get("tconst") or None)
        apply_override(con, key, tconst, note=body.get("note"))
        return {"ok": True, "show_key": key, "tconst": tconst,
                "status": "confirmed" if tconst else "rejected"}

    @app.get("/searches")
    def searches_page(request: Request, con: Db = Depends(get_db)):
        ctx = _base(request, con, "suchen")
        ctx["searches"] = list_searches(con)
        return templates.TemplateResponse(request, "searches.html", ctx)

    @app.post("/searches")
    def searches_create(name: str = Form(""), category: str = Form(""), type: str = Form(""),
                        decade: str = Form(""), genre: str = Form(""), genre_not: str = Form(""),
                        q: str = Form(""), group: str = Form(""), retro: str = Form(""),
                        upcoming: str = Form(""), include_review: str = Form(None),
                        notify: str = Form(None), con: Db = Depends(get_db)):
        filt = {"type": type, "category": category, "decade": decade, "genre": genre,
                "genre_not": genre_not, "q": q,
                "group": group or None, "retro": retro in ("1", "on", "true"),
                "upcoming": upcoming in ("1", "on", "true")}
        try:
            create_search(con, cfg, name, filt, include_review=include_review is not None,
                          notify=notify is not None)
        except ValueError:
            pass
        return RedirectResponse("/searches", status_code=303)

    @app.get("/searches/{search_id}")
    def searches_detail(search_id: int, request: Request, con: Db = Depends(get_db)):
        s = get_search(con, search_id)
        if s is None:
            return templates.TemplateResponse(request, "not_found.html",
                                              {**_base(request, con, ""), "tconst": f"Suche {search_id}"},
                                              status_code=404)
        ctx = _base(request, con, "suchen")
        ctx.update({"s": s, "hits": hits_for(con, search_id)})
        return templates.TemplateResponse(request, "search_detail.html", ctx)

    @app.post("/searches/{search_id}/update")
    def searches_update(search_id: int, name: str = Form(""), con: Db = Depends(get_db),
                        include_review: str = Form(None), notify: str = Form(None)):
        update_search(con, search_id, name=name, include_review=include_review is not None,
                      notify=notify is not None)
        return RedirectResponse("/searches", status_code=303)

    @app.post("/searches/{search_id}/delete")
    def searches_delete(search_id: int, con: Db = Depends(get_db)):
        delete_search(con, search_id)
        return RedirectResponse("/searches", status_code=303)

    @app.post("/searches/run")
    def searches_run(con: Db = Depends(get_db)):
        run_searches(con, cfg)
        return RedirectResponse("/searches", status_code=303)

    @app.get("/api/searches")
    def api_searches(con: Db = Depends(get_db)):
        return {"searches": list_searches(con)}

    @app.post("/api/searches")
    def api_searches_create(body: SearchCreate, con: Db = Depends(get_db)):
        try:
            sid = create_search(con, cfg, body.name, body.filter, include_review=body.include_review,
                                notify=body.notify)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"id": sid}

    @app.put("/api/searches/{search_id}")
    def api_searches_update(search_id: int, body: SearchUpdate, con: Db = Depends(get_db)):
        update_search(con, search_id, name=body.name, include_review=body.include_review,
                      notify=body.notify)
        return {"ok": True}

    @app.delete("/api/searches/{search_id}")
    def api_searches_delete(search_id: int, con: Db = Depends(get_db)):
        delete_search(con, search_id)
        return {"ok": True}

    @app.get("/api/searches/{search_id}/hits")
    def api_searches_hits(search_id: int, con: Db = Depends(get_db)):
        s = get_search(con, search_id)
        if s is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"search": s, "hits": hits_for(con, search_id)}

    @app.post("/api/jobs/{kind}")
    def api_jobs_start(kind: str, con: Db = Depends(get_db)):
        if kind not in ("epg", "imdb", "posters"):
            return JSONResponse({"error": "not found"}, status_code=404)
        result = start_job(con, cfg, kind)
        return JSONResponse(result, status_code=202 if result.get("started") else 409)

    @app.get("/partials/jobs")
    def jobs_partial(request: Request, con: Db = Depends(get_db)):
        ctx = _base(request, con, "status")
        ctx.update({"jobs": recent_jobs(con), "job_running": running_job(con) is not None})
        return templates.TemplateResponse(request, "_jobs.html", ctx)

    @app.get("/api/jobs")
    def api_jobs(con: Db = Depends(get_db)):
        return {"running": running_job(con) is not None, "jobs": recent_jobs(con)}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: int, con: Db = Depends(get_db)):
        job = get_job(con, job_id)
        if job is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return dict(job)

    @app.get("/status")
    def status_page(request: Request, con: Db = Depends(get_db)):
        data = collect(con, cfg)
        ctx = _base(request, con, "status")
        ctx["s"] = data
        ctx["jobs"] = recent_jobs(con)
        ctx["job_running"] = running_job(con) is not None
        ctx["schedule_enabled"] = cfg.schedule_enabled
        return templates.TemplateResponse(request, "status.html", ctx)

    @app.get("/api/status")
    def api_status(con: Db = Depends(get_db)):
        return JSONResponse(collect(con, cfg))

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    return app


app = create_app()
