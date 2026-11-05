"""FastAPI-App: Jetzt, Stöbern, Werk-Detail, Status, JSON-API, Healthcheck."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from zoneinfo import ZoneInfo

from .. import __version__
from ..browse import (annotate_next, apply_filters, catalog_facets, evening_airings, find_work,
                      genre_de, load_catalog, parse_filters, running_airings, sort_works)
from ..config import Config, load_config
from ..db import Db, connect
from ..matching import search_titles
from ..review import apply_override, list_review, review_count, show_key_for, undo_last
from ..status import collect, fmt_num, fmt_size, fmt_ts

HERE = Path(__file__).parent


def _qs(f: dict) -> str:
    """Filter als Query-String (nur Abweichungen vom Standard)."""
    parts: list[str] = []
    if f["type"]:
        parts.append("type=" + ",".join(sorted(f["type"])))
    if f["decade"]:
        parts.append("decade=" + ",".join(str(d) for d in sorted(f["decade"])))
    if f["group"]:
        parts.append("group=" + f["group"])
    if f["genre"]:
        parts.append("genre=" + ",".join(sorted(f["genre"])))
    if f["status"] and f["status"] != {"sicher", "unsicher"}:
        parts.append("status=" + ",".join(sorted(f["status"])))
    if f["retro"]:
        parts.append("retro=1")
    if f["q"]:
        parts.append("q=" + quote(f["q"]))
    if f["sort"] and f["sort"] != "termin":
        parts.append("sort=" + f["sort"])
    if f.get("page", 1) > 1:
        parts.append("page=" + str(f["page"]))
    return "&".join(parts)


def _toggle(f: dict, key: str, value=None) -> dict:
    new = {**f, "page": 1, "type": None if f["type"] is None else set(f["type"]),
           "decade": None if f["decade"] is None else set(f["decade"]),
           "status": None if f["status"] is None else set(f["status"]),
           "genre": None if f["genre"] is None else set(f["genre"])}
    if key in ("type", "decade", "genre", "status"):
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
    return new


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="TV-Guide", version=__version__)
    app.state.cfg = cfg
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["num"] = fmt_num
    templates.env.filters["size"] = fmt_size
    templates.env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, cfg.timezone, fmt)
    templates.env.globals["genre_de"] = genre_de
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
        review = con.execute("SELECT count(*) FROM show_match WHERE status = 'review'").fetchone()[0]
        searches = con.execute("SELECT count(*) FROM saved_searches").fetchone()[0]
        return {"active": active, "review": review, "searches": searches}

    def _at(request: Request) -> int:
        raw = request.query_params.get("at")
        return int(raw) if raw and raw.isdigit() else int(time.time())

    def _base(request, con, active) -> dict:
        return {"request": request, "version": __version__, "nav": _nav(con, active),
                "qs": _qs, "toggle": _toggle, "tz": cfg.timezone}

    @app.get("/")
    def index(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        ctx = _base(request, con, "jetzt")
        ctx.update({
            "f": f, "facets": catalog_facets(works, f, cfg), "at": at,
            "running": running_airings(works, at, f, cfg),
            "evening": evening_airings(works, at, f, cfg),
        })
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.get("/browse")
    def browse(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        selected = sort_works(apply_filters(works, f, cfg), f, at)
        total = len(selected)
        per_page = 60
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(f["page"], pages)
        f = {**f, "page": page}
        shown = annotate_next(selected[(page - 1) * per_page: page * per_page], at)
        ctx = _base(request, con, "stoebern")
        ctx.update({"f": f, "facets": catalog_facets(works, f, cfg), "at": at,
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
        ctx = _base(request, con, "")
        ctx.update({"w": w, "groups": groups})
        return templates.TemplateResponse(request, "work.html", ctx)

    @app.get("/api/now")
    def api_now(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = load_catalog(con, cfg)
        return {"at": at,
                "running": [{"tconst": x["work"]["tconst"], "title": x["work"]["title"], **x["airing"]}
                            for x in running_airings(works, at, f, cfg)],
                "evening": [{"tconst": x["work"]["tconst"], "title": x["work"]["title"], **x["airing"]}
                            for x in evening_airings(works, at, f, cfg)]}

    @app.get("/api/browse")
    def api_browse(request: Request, con: Db = Depends(get_db)):
        at = _at(request)
        f = parse_filters(request.query_params)
        works = sort_works(apply_filters(load_catalog(con, cfg), f, cfg), f, at)
        return {"total": len(works),
                "works": [{k: w[k] for k in ("tconst", "title", "year", "end_year", "type", "kind",
                                             "genres", "rating", "votes", "status", "retro")}
                          | {"count": len(w["airings"])} for w in works]}

    @app.get("/api/works/{tconst}")
    def api_work(tconst: str, con: Db = Depends(get_db)):
        w = find_work(load_catalog(con, cfg), tconst)
        if w is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return w

    @app.get("/review")
    def review_page(request: Request, con: Db = Depends(get_db)):
        per_page = 25
        total = review_count(con)
        pages = max(1, (total + per_page - 1) // per_page)
        raw = request.query_params.get("page", "1")
        page = min(max(1, int(raw) if raw.isdigit() else 1), pages)
        shows = list_review(con, cfg, limit=per_page, offset=(page - 1) * per_page)
        ctx = _base(request, con, "review")
        ctx.update({"shows": shows, "total": total, "page": page, "pages": pages})
        return templates.TemplateResponse(request, "review.html", ctx)

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
    def review_search(request: Request, con: Db = Depends(get_db), q: str = "", show_key: str = ""):
        results = search_titles(con, q, 20) if q.strip() else []
        ctx = {"request": request, "results": results, "show_key": show_key, "q": q}
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

    @app.get("/status")
    def status_page(request: Request, con: Db = Depends(get_db)):
        data = collect(con, cfg)
        ctx = _base(request, con, "status")
        ctx["s"] = data
        return templates.TemplateResponse(request, "status.html", ctx)

    @app.get("/api/status")
    def api_status(con: Db = Depends(get_db)):
        return JSONResponse(collect(con, cfg))

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    return app


app = create_app()
