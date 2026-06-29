"""Django integration for @octri/python.

Call ``init(...)`` once at startup (e.g. in ``settings.py``), then add the
middleware:

    MIDDLEWARE = [
        # ...
        "octri.django.OctriMiddleware",
    ]

``process_exception`` fires when a view raises an unhandled exception; it reports
to Octri and returns ``None`` so Django's normal 500 handling proceeds.
"""

from __future__ import annotations

from typing import Any, Optional

from . import capture_error, capture_span, new_span_id, trace_from_header
from . import _now_iso, _set_current_span, _reset_current_span


class OctriMiddleware:
    """Reports unhandled Django view exceptions to Octri AND times each request
    as a server span (a child of the client SDK span via ``traceparent``)."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        start = _now_iso()
        span_id = new_span_id()
        trace = trace_from_header(request.headers.get("traceparent"))
        # Make this the active span so sub-spans nest under it (same thread).
        token = _set_current_span(trace.trace_id, span_id)
        try:
            response = self.get_response(request)
        finally:
            _reset_current_span(token)
        try:
            status_code = getattr(response, "status_code", 200)
            capture_span(
                trace_id=trace.trace_id,
                span_id=span_id,
                parent_span_id=trace.parent_span_id,
                name=f"{request.method} {request.path}".strip(),
                service="server",
                start_time=start,
                end_time=_now_iso(),
                status="error" if status_code >= 500 else "ok",
            )
        except Exception:
            pass
        return response

    def process_exception(self, request: Any, exception: BaseException) -> Optional[Any]:
        try:
            capture_error(
                exception,
                trace=trace_from_header(request.headers.get("traceparent")),
                method=request.method,
                path=request.path,
                status_code=500,
            )
        except Exception:
            # Never let monitoring break the request.
            pass
        return None
