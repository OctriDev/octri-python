# octri (Python)

**Error and performance monitoring for Python backends.** Report errors out of
Flask, FastAPI, or Django with original-source context per stack frame, time
every request into a waterfall, and join each server error to the client SDK
error for the same request through the W3C `traceparent` header. In the
dashboard you see the full client → server stack under one trace.

Octri turns an OpenAPI spec into a documentation site, client SDKs for ten
languages, an MCP server your AI assistant can call, and monitoring for the
API behind them. This package is the Python monitoring runtime, and it works
on its own: a generated Octri API SDK is not required. See
[octri.dev/monitoring](https://octri.dev/monitoring).

Python 3.8 or newer. Pure standard library, with no required dependencies. The
Python sibling of [`@octri/node`](https://github.com/octridev/octri-node).

## Install

```bash
pip install octri
```

## Setup

```python
from octri import init

init(
    url="https://monitoring.example.com",   # your monitoring base URL
    token=os.environ["OCTRI_TOKEN"],         # your project ingest token
    environment="<your project id>",         # the dashboard project id
    release=os.environ.get("GIT_SHA"),       # optional
)
```

Hosted users can copy the project-scoped URL, token, and environment from the
Monitoring connection settings (or its API). Pass `token=None` only when
pointing at an open self-hosted ingest endpoint. Every request carries an
idempotency key.

## Standalone events

No generated API SDK is required to send your own events:

```python
from octri import capture_event

capture_event(
    "checkout.completed",
    user={"id": customer.id},
    tags={"region": "eu-west", "plan": "growth"},
    context={"order_id": order.id, "total": order.total},
)
```

Delivery is best-effort and happens off the calling thread. Pass `event_id` to
make a retried delivery idempotent.

### Flask

```python
from octri.flask import octri_flask

app = Flask(__name__)
octri_flask(app)   # observes unhandled request exceptions; nothing else to wire
```

### FastAPI / Starlette

```python
from octri.fastapi import OctriMiddleware

app = FastAPI()
app.add_middleware(OctriMiddleware)   # reports unhandled 500s, then re-raises
```

### Django

```python
# settings.py — after calling init(...) somewhere at startup
MIDDLEWARE = [
    # ...
    "octri.django.OctriMiddleware",
]
```

## How linking works

The Octri client SDK sends a `traceparent` header on every request. Each
integration reads it, captures the failing exception (with source context for
each in-app frame), and reports it tagged `octri.origin=server` under the same
`traceId` — so the client SDK error and this server error appear as one linked
trace in the dashboard.

For compiled/minified clients, the dashboard pairs this with source maps / source
bundles; the Python server frames already carry their original source inline.

## Spans & the request waterfall

The middleware times each request. To see where time goes inside it, let Octri
instrument common I/O automatically, or open spans yourself.

### Automatic

```python
octri.auto_instrument()                       # traces requests + urllib (outbound HTTP)
octri.instrument(cursor, ["execute"], op="db")  # your own DB client / util module, once
octri.instrument(cache, ["get", "set"], op="cache")

@octri.traced(op="fn")
def compute_totals(orders): ...
```

Every instrumented call (and every outbound HTTP request) becomes a sub-span
under the current request — no per-call code. Calls to your monitoring backend
are never traced (no feedback loop).

### Manual

```python
with octri.span("orders.list", op="db"):
    rows = db.query(sql)

s = octri.start_span("render", op="view")
# ...work...
s.finish()
```

`op` ("db", "cache", "http", …) colour-codes the bar in the dashboard waterfall.

## Manual capture

```python
from octri import capture_error, trace_from_header

try:
    ...
except Exception as exc:
    capture_error(exc, trace=trace_from_header(request.headers.get("traceparent")))
    raise
```

---

## The rest of Octri

| Product | What it does |
|---|---|
| [API Studio](https://octri.dev/api-studio) | Your OpenAPI spec becomes a hosted documentation site with a live request playground, editable page by page. |
| [SDK Studio](https://octri.dev/sdk-studio) | The same spec becomes client libraries for ten languages, versioned and released together. |
| [MCP](https://octri.dev/mcp) | Your endpoints and docs become tools an AI assistant can call, generated from the same spec. |
| [Monitoring](https://octri.dev/monitoring) | Errors, traces, uptime and releases for the API, joined to the SDK calls that reached it. |

### Monitoring runtimes

[Node](https://github.com/octridev/octri-node) ·
[Python](https://github.com/octridev/octri-python) ·
[Go](https://github.com/octridev/octri-go) ·
[Ruby](https://github.com/octridev/octri-ruby) ·
[Rust](https://github.com/octridev/octri-rust) ·
[PHP](https://github.com/octridev/octri-php) ·
[Java](https://github.com/octridev/octri-java) ·
[Kotlin](https://github.com/octridev/octri-kotlin) ·
[Swift](https://github.com/octridev/octri-swift) ·
[Dart](https://github.com/octridev/octri-dart)

[Documentation](https://docs.octri.dev/docs) ·
[Pricing](https://octri.dev/pricing) ·
[Changelog](https://docs.octri.dev/changelog)

MIT licensed.
