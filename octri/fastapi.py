"""FastAPI / Starlette integration for @octri/python.

    from octri import init
    from octri.fastapi import OctriMiddleware

    init(url=..., token=..., environment=...)
    app = FastAPI()
    app.add_middleware(OctriMiddleware)

The ASGI middleware reports unhandled exceptions to Octri, then re-raises so your
framework still returns its normal 500. Handled exceptions (e.g. ``HTTPException``)
are resolved by FastAPI's own exception middleware below this one and never reach
it, so 404s and validation errors aren't reported.
"""

from __future__ import annotations

from typing import Any

from . import capture_error, trace_from_header


class OctriMiddleware:
    """ASGI middleware that reports unhandled exceptions to Octri."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            try:
                headers = {
                    key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in scope.get("headers", [])
                }
                capture_error(
                    exc,
                    trace=trace_from_header(headers.get("traceparent")),
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status_code=500,
                )
            except Exception:
                # Never let monitoring swallow or mask the originating error.
                pass
            raise
