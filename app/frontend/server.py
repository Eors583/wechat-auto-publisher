from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse


def _dist_dir() -> Path:
    override = str(os.getenv("WECHAT_PUBLISHER_FRONTEND_DIST") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    package_dist = (Path(__file__).resolve().parent / "dist").resolve()
    if package_dist.is_dir():
        return package_dist
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / "frontend" / "dist").resolve()


def create_frontend_app() -> FastAPI:
    """Serve the compiled Vue and Element Plus single-page application."""

    dist = _dist_dir()
    index = dist / "index.html"
    app = FastAPI(
        title="公众号内容工作台前端",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": index.is_file(),
                "frontend": "vue-element-plus",
                "dist": str(dist),
            },
            status_code=200 if index.is_file() else 503,
        )

    @app.get("/robots.txt")
    def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/{asset_path:path}")
    def spa(asset_path: str) -> FileResponse:
        if not index.is_file():
            raise HTTPException(status_code=503, detail="前端资源尚未构建")
        requested = (dist / asset_path).resolve()
        try:
            requested.relative_to(dist)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="资源不存在") from exc
        if asset_path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(index)

    return app


def main() -> None:
    port = int(os.getenv("WECHAT_PUBLISHER_FRONTEND_PORT") or "18765")
    uvicorn.run(
        create_frontend_app(),
        host="0.0.0.0",
        port=port,
        log_level=str(os.getenv("WECHAT_PUBLISHER_FRONTEND_LOG_LEVEL") or "info"),
        log_config=None,
    )


if __name__ == "__main__":
    main()
