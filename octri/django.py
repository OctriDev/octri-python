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

from . import capture_error, trace_from_header


class OctriMiddleware:
    """Reports unhandled Django view exceptions to Octri."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        return self.get_response(request)

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
