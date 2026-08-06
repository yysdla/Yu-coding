"""HTTP server for Feishu event callbacks (ProjectLens FastAPI app)."""

from __future__ import annotations

import os


def main() -> None:
    import logging

    import uvicorn

    from project_lens.config import settings

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    host = os.environ.get("PROJECT_LENS_HOST", "0.0.0.0")
    port = int(os.environ.get("PROJECT_LENS_PORT", "8000"))
    reload = settings.env == "development" and os.environ.get("PROJECT_LENS_RELOAD", "1") != "0"

    uvicorn.run(
        "project_lens.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
