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

from . import capture_error, capture_span, new_span_id, trace_from_header
from . import _now_iso


class OctriMiddleware:
    """ASGI middleware: reports unhandled exceptions AND times each request as a
    server span (a child of the client SDK span via ``traceparent``)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        trace = trace_from_header(headers.get("traceparent"))
        span_id = new_span_id()
        start = _now_iso()
        status = {"code": 200}

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            status["code"] = 500
            try:
                capture_error(
                    exc,
                    trace=trace,
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status_code=500,
                )
            except Exception:
                # Never let monitoring swallow or mask the originating error.
                pass
            raise
        finally:
            try:
                capture_span(
                    trace_id=trace.trace_id,
                    span_id=span_id,
                    parent_span_id=trace.parent_span_id,
                    name=f"{scope.get('method', '')} {scope.get('path', '')}".strip(),
                    service="server",
                    start_time=start,
                    end_time=_now_iso(),
                    status="error" if status["code"] >= 500 else "ok",
                )
            except Exception:
                pass
