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

from typing import Any

from . import capture_error, trace_from_header


def octri_flask(app: Any) -> None:
    """Wire Octri error reporting into a Flask app."""
    from flask import got_request_exception, request

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

    # weak=False so the handler isn't garbage-collected while the app lives.
    got_request_exception.connect(_handler, app, weak=False)
