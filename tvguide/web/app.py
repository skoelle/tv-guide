"""FastAPI-App: Startseite, Statusseite, JSON-Status, Healthcheck."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import Config, load_config
from ..db import Db, connect
from ..status import collect, fmt_num, fmt_size, fmt_ts

HERE = Path(__file__).parent


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="TV-Guide", version=__version__)
    app.state.cfg = cfg
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["num"] = fmt_num
    templates.env.filters["size"] = fmt_size
    templates.env.filters["ts"] = lambda ts, fmt="%d.%m.%Y %H:%M": fmt_ts(ts, cfg.timezone, fmt)

    def get_db():
        con = connect(cfg)
        try:
            yield con
        finally:
            con.close()

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"active": "jetzt", "version": __version__})

    @app.get("/status")
    def status_page(request: Request, con: Db = Depends(get_db)):
        data = collect(con, cfg)
        return templates.TemplateResponse(request, "status.html", {"active": "status", "s": data, "version": __version__})

    @app.get("/api/status")
    def api_status(con: Db = Depends(get_db)):
        return JSONResponse(collect(con, cfg))

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    return app


app = create_app()
