"""Safe static-file serving with SPA fallback."""

from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


class UnsafeStaticPath(ValueError):
    """Raised when a requested path escapes the configured static directory."""


def resolve_static_candidate(static_root: Path, requested_path: str) -> Path:
    """Resolve a URL path while guaranteeing it stays below ``static_root``."""
    root = static_root.resolve()
    normalized = (requested_path or "").replace("\\", "/")
    url_path = PurePosixPath(normalized)

    if normalized.startswith("/") or ".." in url_path.parts:
        raise UnsafeStaticPath(requested_path)

    try:
        candidate = root.joinpath(*url_path.parts).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeStaticPath(requested_path) from exc

    return candidate


def register_spa_routes(app: FastAPI, static_dir: str | Path) -> None:
    """Register static-file serving plus the React SPA index fallback."""
    static_root = Path(static_dir).resolve()
    index_file = resolve_static_candidate(static_root, "index.html")

    @app.get("/{path:path}")
    async def serve_spa(path: str) -> FileResponse:
        try:
            file_path = resolve_static_candidate(static_root, path)
        except UnsafeStaticPath:
            raise HTTPException(status_code=404, detail="Not Found") from None

        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(index_file)
