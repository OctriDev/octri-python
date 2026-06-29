"""@octri/python — server-side error monitoring for Python backends.

Add it to your live API; it reports backend errors to your Octri monitoring
project (with original-source context per stack frame) and links each one to the
client SDK error for the same request via the W3C ``traceparent`` header — so the
dashboard shows the full client -> server stack under one trace.

    from octri import init
    init(url="https://monitoring.example.com", token=..., environment="<project id>")

Then mount the integration for your framework (``octri.flask`` / ``octri.fastapi``
/ ``octri.django``).
"""

from __future__ import annotations

import contextvars
import json
import linecache
import re
import secrets
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional
from urllib import request as _urlrequest

__all__ = [
    "init",
    "capture_error",
    "capture_span",
    "start_span",
    "span",
    "new_span_id",
    "trace_from_header",
    "TraceContext",
    "OctriConfig",
]

_CONTEXT_LINES = 5
_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$", re.IGNORECASE)


class OctriConfig:
    """Resolved reporter configuration."""

    def __init__(self, url: str, token: str, environment: str, release: Optional[str] = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.environment = environment
        self.release = release


_config: Optional[OctriConfig] = None


def init(url: str, token: str, environment: str, release: Optional[str] = None) -> None:
    """Configure the reporter. Call once at startup before mounting middleware.

    Args:
        url: Monitoring base URL, e.g. ``https://monitoring.example.com``.
        token: Ingest token for your project (the same one your SDK uses).
        environment: Project environment / id (the dashboard project id).
        release: Optional release identifier reported with each error.
    """
    global _config
    _config = OctriConfig(url=url, token=token, environment=environment, release=release)


def _hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def new_span_id() -> str:
    """A fresh 64-bit span id (16 hex chars), for a span this service produces."""
    return _hex(8)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Trace context (W3C) ──────────────────────────────────────────────────────


class TraceContext:
    """A request's distributed-trace identity."""

    def __init__(self, trace_id: str, parent_span_id: Optional[str] = None) -> None:
        self.trace_id = trace_id
        # The client's span id (this request's caller), when propagated.
        self.parent_span_id = parent_span_id


def trace_from_header(traceparent: Optional[str]) -> TraceContext:
    """Read the trace from a ``traceparent`` header, or start a fresh trace."""
    if traceparent:
        match = _TRACEPARENT_RE.match(traceparent.strip())
        if match:
            return TraceContext(trace_id=match.group(1), parent_span_id=match.group(2))
    return TraceContext(trace_id=_hex(16))


# ── Frame source context (Python) ────────────────────────────────────────────


def _in_app(filename: str) -> bool:
    path = filename.replace("\\", "/")
    return (
        "/site-packages/" not in path
        and "/dist-packages/" not in path
        and "/lib/python" not in path
        and not path.startswith("<")
    )


def _build_frames(exc: BaseException) -> List[Dict[str, Any]]:
    """Walks an exception's traceback into structured frames with source context."""
    frames: List[Dict[str, Any]] = []
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        filename = code.co_filename
        lineno = tb.tb_lineno
        frame: Dict[str, Any] = {
            "function": code.co_name,
            "filename": filename,
            "lineno": lineno,
            "colno": 0,
            "inApp": _in_app(filename),
        }

        # Original source around the failing line (works for files on disk).
        linecache.checkcache(filename)
        context_line = linecache.getline(filename, lineno)
        if context_line:
            frame["contextLine"] = context_line.rstrip("\n")
            pre = [
                linecache.getline(filename, n).rstrip("\n")
                for n in range(max(1, lineno - _CONTEXT_LINES), lineno)
            ]
            post: List[str] = []
            for n in range(lineno + 1, lineno + 1 + _CONTEXT_LINES):
                raw = linecache.getline(filename, n)
                if raw == "":  # past end of file
                    break
                post.append(raw.rstrip("\n"))
            if pre:
                frame["preContext"] = pre
            if post:
                frame["postContext"] = post

        frames.append(frame)
        tb = tb.tb_next
    # A Python traceback walks outer -> inner; reverse it so the throw site is
    # first, matching V8 stack order (and the dashboard's culprit = top frame).
    frames.reverse()
    return frames


# ── Reporting ────────────────────────────────────────────────────────────────


def capture_error(
    error: BaseException,
    *,
    trace: Optional[TraceContext] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    operation_id: Optional[str] = None,
    status_code: Optional[int] = None,
    level: str = "error",
) -> None:
    """Report an error to the monitoring project (fire-and-forget).

    Tagged ``octri.origin=server`` and stamped with the request's ``traceId`` (from
    ``trace``), so it links to the client SDK error for the same request.
    """
    cfg = _config
    if cfg is None:
        return

    tc = trace or TraceContext(trace_id=_hex(16))
    stack = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    payload: Dict[str, Any] = {
        "eventId": _hex(16),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "method": method,
        "path": path,
        "operationId": operation_id,
        "statusCode": status_code,
        "environment": cfg.environment,
        "release": cfg.release,
        "traceId": tc.trace_id,
        "spanId": _hex(8),
        "tags": {"octri.origin": "server"},
        "error": {
            "name": type(error).__name__,
            "message": str(error),
            "stack": stack,
            "frames": _build_frames(error),
        },
    }
    # Drop unset fields so the wire shape matches @octri/node (undefined omitted).
    payload = {key: value for key, value in payload.items() if value is not None}

    _post_json(cfg, "/ingest", payload)


def capture_span(
    *,
    trace_id: str,
    span_id: str,
    name: str,
    parent_span_id: Optional[str] = None,
    service: str = "server",
    operation_id: Optional[str] = None,
    start_time: str,
    end_time: Optional[str] = None,
    status: str = "ok",
) -> None:
    """Report one span to the monitoring trace store (fire-and-forget).

    Spans sharing a ``trace_id`` form the request waterfall — the client SDK span
    (root) and this server span (its child via ``parent_span_id``) line up under
    one trace in the dashboard.
    """
    cfg = _config
    if cfg is None:
        return
    payload: Dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "service": service,
        "operationId": operation_id,
        "startTime": start_time,
        "endTime": end_time,
        "status": status,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    _post_json(cfg, "/traces", payload)


# ── Sub-spans (where time goes inside a request) ─────────────────────────────
# The active span for the running request, propagated through the request's
# thread / async task so a `start_span` / `span()` call nests under it (and under
# any enclosing sub-span). The framework integration sets it per request.

_current_span: "contextvars.ContextVar[Optional[Dict[str, str]]]" = contextvars.ContextVar(
    "octri_current_span", default=None
)


def _set_current_span(trace_id: str, span_id: str) -> Any:
    return _current_span.set({"trace_id": trace_id, "span_id": span_id})


def _reset_current_span(token: Any) -> None:
    if token is None:
        return
    try:
        _current_span.reset(token)
    except Exception:
        pass


class _NoopSpan:
    def end(self, status: str = "ok") -> None:  # noqa: D401 - matches _SpanHandle
        pass


class _SpanHandle:
    def __init__(
        self, trace_id: str, span_id: str, parent_span_id: str, name: str, service: str, start: str
    ) -> None:
        self._trace_id = trace_id
        self._span_id = span_id
        self._parent_span_id = parent_span_id
        self._name = name
        self._service = service
        self._start = start
        self._ended = False

    def end(self, status: str = "ok") -> None:
        if self._ended:
            return
        self._ended = True
        capture_span(
            trace_id=self._trace_id,
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
            name=self._name,
            service=self._service,
            start_time=self._start,
            end_time=_now_iso(),
            status=status,
        )


def start_span(name: str, op: Optional[str] = None) -> Any:
    """Open a sub-span under the active request span; call ``.end()`` when done.

    Returns a no-op handle outside a request or before ``init()``. ``op`` is a
    category for color-coding the waterfall, e.g. "db", "cache", "http".
    """
    ctx = _current_span.get()
    if _config is None or ctx is None:
        return _NoopSpan()
    return _SpanHandle(
        ctx["trace_id"], new_span_id(), ctx["span_id"], name, op or "server", _now_iso()
    )


@contextmanager
def span(name: str, op: Optional[str] = None) -> Iterator[None]:
    """Time a block as a sub-span under the active request span. Nests correctly.

        with octri.span("orders.list", op="db"):
            rows = db.query(sql)
    """
    ctx = _current_span.get()
    if _config is None or ctx is None:
        yield
        return
    span_id = new_span_id()
    start = _now_iso()
    token = _current_span.set({"trace_id": ctx["trace_id"], "span_id": span_id})
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        _reset_current_span(token)
        capture_span(
            trace_id=ctx["trace_id"],
            span_id=span_id,
            parent_span_id=ctx["span_id"],
            name=name,
            service=op or "server",
            start_time=start,
            end_time=_now_iso(),
            status=status,
        )


def _post_json(cfg: OctriConfig, path: str, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")

    def _post() -> None:
        req = _urlrequest.Request(
            f"{cfg.url}{path}",
            data=body,
            headers={"content-type": "application/json", "authorization": f"Bearer {cfg.token}"},
            method="POST",
        )
        try:
            _urlrequest.urlopen(req, timeout=5).close()  # noqa: S310 (trusted, configured URL)
        except Exception:
            # A reporting failure must never mask the originating error.
            pass

    threading.Thread(target=_post, daemon=True).start()
