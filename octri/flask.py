"""Flask integration for @octri/python.

    from octri import init
    from octri.flask import octri_flask

    init(url=..., token=..., environment=...)
    app = Flask(__name__)
    octri_flask(app)

Uses Flask's ``got_request_exception`` signal, so it observes unhandled request
exceptions without changing your response or error handling.
"""

from __future__ import annotations

from typing import Any, Optional

from . import capture_error, capture_span, new_span_id, trace_from_header
from . import _now_iso


def octri_flask(app: Any) -> None:
    """Wire Octri error reporting + request-timing spans into a Flask app."""
    from flask import g, got_request_exception, request

    def _start() -> None:
        g._octri_span = {
            "start": _now_iso(),
            "span_id": new_span_id(),
            "trace": trace_from_header(request.headers.get("traceparent")),
        }

    def _finish(exc: Optional[BaseException]) -> None:
        span = getattr(g, "_octri_span", None)
        if span is None:
            return
        try:
            capture_span(
                trace_id=span["trace"].trace_id,
                span_id=span["span_id"],
                parent_span_id=span["trace"].parent_span_id,
                name=f"{request.method} {request.path}".strip(),
                service="server",
                start_time=span["start"],
                end_time=_now_iso(),
                status="error" if exc is not None else "ok",
            )
        except Exception:
            pass

    def _handler(_sender: Any, exception: BaseException, **_extra: Any) -> None:
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

    app.before_request(_start)
    # teardown_request always runs (even on an unhandled exception), so the span
    # is reported for both successful and failed requests.
    app.teardown_request(_finish)
    # weak=False so the handler isn't garbage-collected while the app lives.
    got_request_exception.connect(_handler, app, weak=False)
