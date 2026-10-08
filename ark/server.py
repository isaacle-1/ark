"""FastAPI application factory: middleware, routers, lifespan, static SPA, /svc proxy."""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

import ark
from ark.auth import bootstrap_admin
from ark.config import ArkConfig, load_config
from ark.db import Database, run_migrations
from ark.jobs import create_worker
from ark.logging_setup import request_id_var, setup_logging
from ark.models import Setting
from ark.paths import Paths, ensure_data_dirs
from ark.routers import admin as admin_router
from ark.routers import auth as auth_router
from ark.routers import health as health_router
from ark.routers import logs as logs_router
from ark.supervisor import Supervisor, get_sidecar_specs

logger = logging.getLogger("ark.http")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "SAMEORIGIN",
}

_FALLBACK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARK — frontend not built</title>
<style>
 body {{ background:#0b0f14; color:#cfe3ff; font:16px/1.6 system-ui,sans-serif;
       display:flex; min-height:100vh; align-items:center; justify-content:center; margin:0; }}
 main {{ max-width:36rem; padding:2rem; border:1px solid #24405e; border-radius:12px; }}
 code {{ background:#122235; padding:.15rem .4rem; border-radius:4px; }}
 a {{ color:#6db2ff; }}
</style></head>
<body><main>
<h1>ARK {version}</h1>
<p>The web interface has not been built or installed on this server.</p>
<p>API health: <a href="/api/health">/api/health</a> &middot;
   OpenAPI: <a href="/api/docs">/api/docs</a></p>
<p>Build it with <code>make frontend</code> on the server, or install the
release bundle (see README &rarr; Installation).</p>
</main></body></html>
"""


def _gen_request_id(request: Request) -> str:
    inbound = request.headers.get("X-Request-ID", "")
    if _REQUEST_ID_RE.match(inbound):
        return inbound
    return uuid.uuid4().hex


def _apply_security_headers(response: Response) -> None:
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)


def _is_static_path(path: str) -> bool:
    return path.startswith("/assets/") or path in {
        "/favicon.svg",
        "/manifest.webmanifest",
        "/sw.js",
        "/icon-192.png",
        "/icon-512.png",
    }


def create_app(config: ArkConfig | None = None, paths: Paths | None = None) -> FastAPI:
    """Build the FastAPI app (config/logging/db are wired here, not at import)."""
    paths = paths or Paths.current()
    config = config or load_config(paths)
    ensure_data_dirs(paths)
    setup_logging(config, paths, force=True)

    app_state_db = Database(paths.db_file, echo=config.debug)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = time.time()
        app.state.config = config
        app.state.paths = paths
        app.state.db = app_state_db

        try:
            run_migrations(paths)
        except Exception as exc:
            logger.error("database migration failed: %s", exc, exc_info=True)
            raise RuntimeError(
                f"Database migration failed: {exc}\n"
                f"  db: {paths.db_file}\n"
                "  Check data/logs/ark.log and the traceback above; "
                "restore from data/backups/ if the DB is damaged."
            ) from exc

        with app_state_db.session() as session:
            bootstrap_admin(session, config, paths)

        # Runtime log level set from the UI (env var still wins).
        if not os.environ.get("LOG_LEVEL", "").strip():
            with app_state_db.session() as session:
                row = session.get(Setting, "log_level")
                if row is not None:
                    from ark.logging_setup import set_root_level

                    try:
                        set_root_level(row.value)
                    except ValueError:
                        logger.warning("stored log_level setting invalid: %s", row.value)

        worker = create_worker(app_state_db, config, paths)
        app.state.worker = worker
        await worker.start()

        supervisor = Supervisor(paths)
        app.state.supervisor = supervisor
        for spec in get_sidecar_specs(paths):
            supervisor.register(spec)
        await supervisor.start_all()

        import logging as _logging
        from logging import getLevelName

        logger.info(
            "ARK started",
            extra={
                "version": ark.__version__,
                "ark_home": str(paths.home),
                "pid": os.getpid(),
                "host": config.server.host,
                "port": config.server.port,
                "log_level": str(getLevelName(_logging.getLogger().level)),
                "auth_mode": config.auth.mode,
                "debug": config.debug,
            },
        )
        try:
            yield
        finally:
            logger.info("ARK shutting down")
            await worker.stop()
            await supervisor.stop_all()
            app_state_db.dispose()

    app = FastAPI(
        title="ARK — Autonomous Resilience and Knowledge System",
        version=ark.__version__,
        docs_url="/api/docs" if config.server.docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if config.server.docs else None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.paths = paths
    app.state.db = app_state_db

    # --- middleware: request id, timing, access log, security headers ------

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        rid = _gen_request_id(request)
        request.state.request_id = rid
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = request.url.path
            response.headers["X-Request-ID"] = rid
            _apply_security_headers(response)
            status = response.status_code
            if status >= 500:
                level = logging.ERROR
            elif status >= 400:
                level = logging.WARNING
            elif _is_static_path(path):
                level = logging.DEBUG
            else:
                level = logging.INFO
            logger.log(
                level,
                "request finished",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                    "request_id": rid,
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    # --- exception handlers: every error carries request_id ---------------

    def _error_response(
        request: Request, status: int, detail: Any, headers: Mapping[str, str] | None = None
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        resp = JSONResponse(
            {"detail": detail, "request_id": rid}, status_code=status, headers=headers
        )
        resp.headers["X-Request-ID"] = rid
        _apply_security_headers(resp)
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Log the cause (detail) with the request_id so the Logs page shows
        # *why* a 4xx/5xx happened, not just that it happened.
        rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        log = logger.error if exc.status_code >= 500 else logger.warning
        log(
            "request failed",
            extra={
                "status": exc.status_code,
                "method": request.method,
                "path": request.url.path,
                "detail": exc.detail,
                "request_id": rid,
            },
        )
        return _error_response(request, exc.status_code, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": ".".join(str(x) for x in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        logger.warning(
            "request validation failed",
            extra={
                "status": 422,
                "method": request.method,
                "path": request.url.path,
                "errors": errors,
                "request_id": rid,
            },
        )
        return _error_response(request, 422, errors)

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        logger.exception(
            "unhandled server error",
            extra={"request_id": rid, "method": request.method, "path": request.url.path},
        )
        return _error_response(
            request, 500, "Internal server error (see logs; quote the request_id)"
        )

    # --- routers ----------------------------------------------------------

    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(logs_router.router)
    app.include_router(admin_router.router)

    # --- /svc/<name>/... reverse proxy to supervised sidecars -------------

    svc_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

    async def _svc_proxy(name: str, path: str, request: Request) -> Response:
        supervisor: Supervisor = request.app.state.supervisor
        spec = supervisor.specs.get(name)
        if spec is None:
            available = sorted(supervisor.specs) or ["(none configured)"]
            raise HTTPException(
                status_code=404,
                detail=f"Unknown sidecar {name!r}. Available: {', '.join(available)}",
            )
        if not supervisor.is_running(name):
            raise HTTPException(
                status_code=503,
                detail={
                    "sidecar": name,
                    "reason": "not running",
                    "hint": f"Check data/logs/{name}.log and `ark doctor`",
                },
            )
        query = f"?{request.url.query}" if request.url.query else ""
        target = f"http://127.0.0.1:{spec.port}/{path}{query}"
        body = await request.body()
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "connection", "content-length", "accept-encoding"}
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                upstream = await client.request(
                    request.method, target, content=body, headers=fwd_headers
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "sidecar proxy failed",
                extra={"sidecar": name, "target": target, "error": str(exc)},
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "sidecar": name,
                    "reason": f"proxy to 127.0.0.1:{spec.port} failed: {exc}",
                    "hint": f"Check data/logs/{name}.log",
                },
            ) from exc
        headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower()
            not in {"content-length", "transfer-encoding", "content-encoding", "connection"}
        }
        headers["X-Request-ID"] = getattr(request.state, "request_id", uuid.uuid4().hex)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=headers,
            media_type=upstream.headers.get("content-type"),
        )

    @app.api_route("/svc/{name}", methods=svc_methods, include_in_schema=False)
    async def svc_root(name: str, request: Request) -> Response:
        return await _svc_proxy(name, "", request)

    @app.api_route("/svc/{name}/{path:path}", methods=svc_methods, include_in_schema=False)
    async def svc_path(name: str, path: str, request: Request) -> Response:
        return await _svc_proxy(name, path, request)

    # --- static SPA (registered last: catch-all) --------------------------

    dist = paths.frontend_dist

    def _fallback_page() -> HTMLResponse:
        return HTMLResponse(_FALLBACK_HTML.format(version=ark.__version__))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        if full_path.startswith(("api/", "svc/")) or full_path == "healthz":
            raise HTTPException(status_code=404, detail=f"Not found: /{full_path}")
        if dist.is_dir() and full_path:
            base = dist.resolve()
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found") from None
            if candidate.is_file():
                media, _ = mimetypes.guess_type(candidate.name)
                immutable = full_path.startswith("assets/")
                return FileResponse(
                    candidate,
                    media_type=media,
                    headers={
                        "Cache-Control": (
                            "public, max-age=31536000, immutable" if immutable else "no-cache"
                        )
                    },
                )
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(
                index, media_type="text/html", headers={"Cache-Control": "no-cache"}
            )
        return _fallback_page()

    return app
