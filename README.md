# octri (Python)

Server-side error monitoring for **Python** backends. Add it to your live API and
it reports backend errors to your Octri monitoring project — with original-source
context per stack frame — and **links each one to the client SDK error for the
same request** via the W3C `traceparent` header. In the dashboard you then see
the full client → server stack under one trace.

The Python sibling of [`@octri/node`](../octri-node). Pure standard library —
no required dependencies.

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

## Manual capture

```python
from octri import capture_error, trace_from_header

try:
    ...
except Exception as exc:
    capture_error(exc, trace=trace_from_header(request.headers.get("traceparent")))
    raise
```
