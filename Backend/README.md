# Internal Document Intelligence API

An async FastAPI chat endpoint backed by a three-agent LangGraph pipeline over internal
company documents (policies, architecture docs, runbooks, incident reports, product
specs, meeting notes), plus a Streamlit frontend that shows the pipeline's internal
state in real time.

The document set is a small seed corpus (`app/mock/documents.py`), but retrieval over
it is real hybrid search — dense embeddings + BM25, fused into one ranking. See
[Hybrid search](#hybrid-search) below.

```bash
# terminal 1
pip install -r requirements.txt
cp .env.example .env && $EDITOR .env   # set LLM_API_KEY
uvicorn app.main:app --reload   # first start downloads the embedding model (~130MB), then caches it

# terminal 2
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```
Open the Streamlit URL, log in with one of the three demo accounts shown in the
sidebar (`viewer` / `viewer123`, or `analyst`, or `admin` — passwords match the
username), and chat. See [Frontend](#frontend) and [Roles and tool access](#roles-and-tool-access) below.

## Architecture

```
POST /chat
    │
    ▼
┌─────────────────┐   in scope + needs docs   ┌─────────────────┐
│  Orchestration  │──────────────────────────▶│    Retrieval    │
│     Agent       │                           │      Agent      │
│                 │                           │  (tool calling) │
│ intent · scope  │   out of scope / chitchat  └────────┬────────┘
│    · routing    │──────────────────┐                  │
└─────────────────┘                  ▼                  │
                            ┌─────────────────┐         │
                            │    Response     │◀────────┘
                            │ Generation Agent│
                            └────────┬────────┘
                                     ▼
                                 ChatResponse
```

Each agent's behaviour lives in a markdown instruction file under
`app/agents/instructions/` — `orchestrator.md`, `retrieval.md`, `response.md`. Edit
those to change agent behaviour; no Python change needed.

### Orchestration agent
Structured-output call that produces intent, an intent type, a scope verdict and a
route. Out-of-scope requests skip retrieval entirely and go straight to the response
agent with guidance for declining gracefully. If the orchestrator itself fails, the
request falls back to retrieval rather than erroring.

### Retrieval agent
A tool-calling agent (`create_react_agent`) with six tools:

| Tool | What it does |
|---|---|
| `document_search` | Searches the RAG store, returns documents with content |
| `metadata_retrieval` | Given a document type, returns its metadata fields and the values actually in use |
| `filter_by_metadata` | Exact-match metadata filter — returns the **count** plus an id/title index, and caches the matched documents in the session |
| `analyze_documents` | Analyses the cached set; **fans out to parallel sub-agents** when it is large, then merges their findings |
| `employee_directory` | **MCP** — employee lookup via the mock REST API |
| `service_catalog` | **MCP** — service ownership, tier, on-call and dependencies via the mock REST API |

Tool failures are returned to the agent as `{"error": ...}` rather than raised, so a
single bad call degrades the answer instead of failing the request.

### Response generation agent
The only agent that addresses the user. Grounds every claim in the retrieval agent's
evidence brief, cites document ids inline, flags stale or draft sources, and handles
the out-of-scope and clarification cases. Streams its answer token-by-token
(`llm.astream`) rather than waiting for the whole response, so the chat window can
render it incrementally.

## Roles and tool access

Three hardcoded demo accounts, one per role (`app/auth.py` — plaintext, no tokens,
demo only, by design):

| Role | Username / password | Tools |
|---|---|---|
| Viewer | `viewer` / `viewer123` | `document_search` |
| Analyst | `analyst` / `analyst123` | `document_search`, `metadata_retrieval`, `filter_by_metadata`, `analyze_documents` |
| Admin | `admin` / `admin123` | all analyst tools, plus `employee_directory`, `service_catalog` |

This is enforced by construction, not by prompting. `build_retrieval_agents()` builds
one **separate `create_react_agent` per role** at startup, each bound only to the
tools `ROLE_TOOLS` grants — a viewer's agent object has no `employee_directory` tool
in it at all, so the model has nothing to call even if it tried. Confirmed live: a
viewer asked "who owns the payment service and who's on call" never invokes
`employee_directory`/`service_catalog` (they aren't bound) and instead answers from
documents alone; the same question from `admin` correctly calls both.

`POST /auth/login` checks the password once; after that, the caller's role is
resolved server-side on every `/chat`/`/chat/stream` call from the `X-User-Id`
header (the same header rate limiting already uses for caller identity) via a
lookup the client cannot influence — an unrecognised or missing header defaults to
`viewer`, the least-privileged role, rather than rejecting the request.

## Frontend

`frontend/app.py` is a Streamlit chat client, kept deliberately thin: it calls
`/auth/login`, `/session` and `/chat/stream` over plain HTTP and renders what the
backend already reports. It does not call an LLM or the graph directly.

```bash
pip install -r frontend/requirements.txt
streamlit run frontend/app.py
```

**Chat window** (left column) — multi-turn history via `st.chat_message`, with the
answer streamed in token-by-token as it arrives.

**Agent Activity Panel** (right column) — every internal step, as it happens, via
`st.status()`:

| Requirement | Where it comes from |
|---|---|
| Current agent state | The headline `st.status()` label, updated on every event to that event's own message — one source of truth, not a duplicate mapping |
| Active LangGraph node | `node_start`/`node_end` events from the orchestrator, retrieval and response nodes |
| Tool calls being executed | `ActivityCallbackHandler` (`app/activity.py`) — a standard LangChain callback attached to the retrieval agent's invocation, so it fires for every tool call uniformly, RAG and MCP alike, with no per-tool code |
| Retrieval status | Narration from inside the retrieval agent and the `analyze_documents` fan-out (e.g. "13 documents exceed the inline threshold - fanning out to 3 sub-agents") |
| Memory updates | Session cache writes (`filter_by_metadata`) and conversation-history writes (start/resume of a session, turn appended at the end) |
| Validation results | The orchestrator's scope check (in scope? routed where?) and the retrieval node's evidence check (found something, or not, or cut off by the step budget) |
| Final response generation | `node_start`/token events on the response node, then a terminal `final` event carrying the full response payload |

Past turns collapse into an expander (`✅`/`🛑` icon by outcome); the current turn
stays expanded while it streams. "New chat" mints a fresh session id via `/session`
and clears history; "Log out" clears the login.

## Session and streaming endpoints

**`POST /session`** → `{"session_id": "<uuid>"}`. Mints an empty session ahead of the
first message, so the frontend has a session id to display and reuse from the start
of the conversation, not just after the first reply.

**`POST /chat/stream`** — same request body as `/chat` (`message`, optional
`session_id`), same role resolution via `X-User-Id`, same rate limiting. Instead of
one JSON response it streams NDJSON (`application/x-ndjson`): one
`app.schemas.ActivityEvent` per line —

```json
{"type": "node_start", "node": "retrieval", "message": "Gathering evidence (role: admin)", "data": {}}
{"type": "tool_start", "tool": "service_catalog", "message": "Calling service_catalog", "data": {...}}
{"type": "token", "node": "respond", "message": "", "data": {"text": "The "}}
{"type": "final", "message": "Response generated", "data": {"session_id": "...", "answer": "...", "sources": [...], ...}}
```

ending in either a `"final"` line (the completed `ChatResponse`, embedded in `data`)
or an `"error"` line — the stream itself always starts with `200 OK`, so a mid-turn
failure is reported as the last line rather than an HTTP error. (A request rejected
*before* streaming starts, e.g. `429` from the rate limiter, is still a normal single
JSON `ErrorResponse` — the frontend checks the status code before trying to parse
NDJSON.) `/chat` is unchanged and kept for non-streaming/simple callers — it's a
thin wrapper that drains the same stream and returns just the final payload.

## Analytics fan-out

`filter_by_metadata` caches its matches in the session. `analyze_documents` then reads
that cache:

- **≤ `ANALYTICS_FANOUT_THRESHOLD` documents** (default 10) → one inline analysis.
- **More than that** → split into batches of `ANALYTICS_CHUNK_SIZE` (default 5), run up
  to `ANALYTICS_MAX_SUBAGENTS` sub-agents concurrently with `asyncio.gather`, then a
  merge pass folds the partials into one result.

Sub-agent failures are isolated: surviving batches still produce an answer, and
`failed_subagents` reports how many were lost.

## MCP tools

`app/mcp_server/server.py` is a stdio MCP server exposing `employee_directory` and
`service_catalog`. Both call the mock REST API over HTTP (`app/mock/mock_api.py`,
mounted at `/mock-api` on this same app), so they exercise a real network hop — point
`MOCK_API_BASE_URL` at the real HR / catalog service and the tools are production-ready.

The server is launched as a subprocess at startup. If it fails to start, the app logs
the failure and continues with the four RAG tools; the retrieval agent is told the
directories are unavailable.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then set LLM_API_KEY
python -m scripts.list_models   # see what your gateway serves, then set MODEL
uvicorn app.main:app --reload
```

The app fails fast at startup if `LLM_API_KEY` is missing. It also asks the gateway
for its model list at startup and logs a warning (`llm.model_missing`) if the
configured `MODEL` is not on it — a bad model id shows up in the logs, not as a
mystery 400 on the first request.

## Usage

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What does the payment security policy require for storing card data?"}'
```

```json
{
  "session_id": "3f2a...",
  "request_id": "9c17f634247f",
  "answer": "Payment credentials must be encrypted in transit and at rest [POL-001] ...",
  "intent": "Find the storage requirements in the payment security policy.",
  "route": "retrieval",
  "in_scope": true,
  "sources": [{"document_id": "POL-001", "title": "Payment Security Policy", "document_type": "policy"}],
  "tools_used": ["document_search"],
  "degraded": false
}
```

Pass the returned `session_id` back on the next call to keep conversation history and
the cached filter results that `analyze_documents` works on.

Things worth trying:

- `"Who owns the payment service and who is on call?"` → MCP tools
- `"How many active policies do we have?"` → `filter_by_metadata`
- `"What are the recurring root causes across our Engineering incidents?"` → filter then analytics fan-out
- `"What's the weather in Paris?"` → graceful out-of-scope decline

### Other endpoints

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | Demo login — checks a hardcoded username/password, returns the role |
| `POST /session` | Mints a fresh chat session id |
| `POST /chat/stream` | Same as `/chat`, but streams NDJSON activity events — see below |
| `GET /health` | Status, model, gateway URL, whether MCP is connected, known document types, the role→tools map, whether dense search warmed up |
| `GET /mock-api/employees` · `/services` | The mock backend the MCP tools consume |
| `GET /docs` | OpenAPI UI |

## Async, errors and logging

**Async throughout** — the endpoint, the graph, every tool, the RAG facade, the MCP
calls and the analytics fan-out. Nothing blocks the event loop.

**Errors** — domain exceptions in `app/errors.py` carry a status code and a stable
code. Handlers in `app/main.py` turn every failure, including unhandled ones, into a
structured `ErrorResponse` with a request id. Each layer degrades rather than
propagating where it usefully can: tools return errors to the agent, the orchestrator
falls back to retrieval, retrieval failure still yields an honest answer, and the
`degraded` flag on the response says when the answer was produced with less than the
full pipeline.

**Logging** — JSON to stdout (`app/logging_config.py`), one object per line, with
`request_id` and `session_id` on every line via context vars. Key events:
`chat.started`, `node.orchestrator`, `tool.document_search`, `tool.analytics`,
`tool.analytics.subagent`, `node.retrieval`, `node.response`, `chat.completed`
(with `duration_ms`).

## LLM provider

The app talks to any **OpenAI-compatible** `/v1/chat/completions` gateway through
`langchain-openai`, configured by two settings:

```
LLM_BASE_URL=https://api.ai.kodekloud.com/v1
LLM_API_KEY=...
```

Changing provider is a `.env` change, not a code change — `app/llm.py` is the only
module that constructs a model.

The orchestration agent uses structured output and the retrieval agent uses tool
calling, so **the model you pick must support function/tool calling.** A model without
it will fail on the orchestrator's structured-output call.

## Rate limiting

`POST /chat` is protected by a token bucket per caller (`app/rate_limit.py`). Every
other endpoint — `/health`, `/mock-api/*` — is unlimited: the MCP server calls
`/mock-api` over HTTP several times *during* a single `/chat` turn, so limiting it
would make one chat request throttle itself.

**Identity.** There's no authentication in this app beyond the demo login, so the
caller is identified by an `X-User-Id` request header, falling back to the client IP
when it's absent — the same header the role resolver (`app/auth.py`) reads, so one
header does double duty as both the rate-limit key and the tool-access identity:

```bash
curl -X POST http://127.0.0.1:8000/chat -H "X-User-Id: alice"   -H "Content-Type: application/json" -d '{"message": "..."}'
```

`session_id` was deliberately not used as the key — it's client-chosen and
unvalidated, so a caller could bypass a per-session limit just by omitting it.
Swapping in real auth later is a one-function change (`resolve_client_id`).

**Algorithm.** Each caller gets an independent bucket that refills continuously
(`tokens/sec = RATE_LIMIT_REQUESTS / RATE_LIMIT_WINDOW_SECONDS`), rather than
resetting in fixed windows — that avoids the thundering-herd retry spike a fixed
window causes right after it rolls over. Refill is lazy (computed from elapsed time
on each request, no background task) and uses `time.monotonic()` so a wall-clock
jump can't stall or over-fill a bucket. `RATE_LIMIT_BURST` sets bucket capacity
independently of the refill rate — how much saved-up allowance a caller can spend at
once — and defaults to the same value as `RATE_LIMIT_REQUESTS`.

**Configuration** (`.env.example`):

| Setting | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Set `false` to disable entirely |
| `RATE_LIMIT_REQUESTS` | `20` | Tokens refilled per window — the sustained rate |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window the refill rate is expressed over |
| `RATE_LIMIT_BURST` | `0` | Bucket capacity; `0` means "same as requests" |
| `RATE_LIMIT_IDLE_TTL_SECONDS` | `900` | Idle buckets are dropped after this long |

**On rejection**, the caller gets a `429` through the normal `ErrorResponse` envelope,
plus `Retry-After`, `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers:

```json
{
  "code": "rate_limited",
  "message": "Rate limit exceeded. Try again in 26 seconds.",
  "request_id": "9a7b8579acc3",
  "details": {"limit": 20, "window_seconds": 60.0, "retry_after_seconds": 26}
}
```

Enforcement is a FastAPI dependency on the route, not middleware — that keeps it
inside `correlation_middleware` (so a 429 still carries `request_id` and the
`x-request-id` header) and keeps it scoped to `/chat` without an exemption list.

## Configuration

All other settings are environment variables (see `.env.example`). `MODEL` and
`SUBAGENT_MODEL` both default to `gpt-oss-120b` — confirm against
`python -m scripts.list_models`, since the gateway decides what is available. Set
`SUBAGENT_MODEL` to a cheaper model if analytics fan-outs get expensive.
`ENABLE_MCP=false` runs without the directory tools.

## Tests

```bash
pytest
```

No API key or network needed, including on a cold cache — LLM calls are stubbed
throughout and dense search is disabled session-wide by a `conftest.py` fixture
(BM25 alone is enough to exercise the tool layer; `tests/test_rag.py` injects its
own fake embedder to test the fusion math without ever loading the real model).
Coverage: the tools and their failure paths, graph routing (retrieval / direct /
out-of-scope), evidence and source propagation, degradation when the orchestrator or
retrieval fails, the analytics inline/fan-out split including partial and total
sub-agent failure, rate limiting (bucket math against a fake clock, per-caller
isolation, HTTP-level 429/header/exemption behaviour), roles (the exact tool list
per role, role resolution and its fail-closed default), activity events (emission,
the tool-call callback handler, context propagation across concurrent tasks), hybrid
search (BM25 ranking, cosine similarity, fusion weighting at `alpha=0`/`1`/`0.5`,
type-filter-before-cut correctness, concurrent warm-up, degradation when embeddings
fail at warm-up or per-query), and `/session` / `/auth/login` / `/chat/stream` HTTP
framing — all via `httpx.ASGITransport` against the real app with the graph stubbed,
never a real server process or `lifespan` (which would spawn the MCP subprocess and
call the LLM gateway).

Beyond the automated suite, the full stack (backend + Streamlit) was driven through
a real browser for this feature: login, per-role tool access (viewer genuinely
cannot invoke the MCP tools; admin can), multi-turn memory, and the activity panel
updating live mid-stream were all observed directly, not just asserted in tests.

## Layout

```
app/
  main.py              FastAPI app, exception handlers, correlation middleware, all endpoints
  chat_service.py      Session handling + graph invocation; stream_chat() feeds both /chat and /chat/stream
  config.py            Settings
  logging_config.py    JSON logging with request/session context
  errors.py            Domain exceptions
  schemas.py           Request/response models, including ActivityEvent
  llm.py               Chat model factory + gateway model check
  session.py           In-memory session + document cache
  rate_limit.py        Token bucket rate limiting (per-caller, /chat + /chat/stream only)
  auth.py              Hardcoded demo users, roles, per-role tool lists, role resolution
  activity.py          Real-time activity events: contextvar sink, emit(), the tool-call callback handler
  agents/
    loader.py          Loads instruction files
    instructions/      orchestrator.md · retrieval.md · response.md
  graph/
    builder.py         Graph wiring
    state.py           Shared state (includes role)
    nodes/             orchestrator.py · retrieval.py (role-scoped agents) · response.py (streams the answer)
  tools/
    rag_tools.py       The four RAG tools + analytics fan-out + activity emit calls
    mcp_tools.py       MCP client loading, degrades if unavailable
  mcp_server/
    server.py          Stdio MCP server (employee directory, service catalog)
  mock/
    mock_api.py        Mock REST API the MCP tools call
    documents.py       Seed corpus
  rag/
    embeddings.py      Local dense embeddings (fastembed) + cosine similarity
    sparse.py          BM25 index (rank_bm25)
    store.py           HybridRagStore - fuses both into one ranking; also the exact-match lookups
frontend/
  app.py               Streamlit chat client (login, session, streaming chat, activity panel)
  requirements.txt     streamlit, requests
tests/
```

## Hybrid search

`app/rag/` implements real hybrid retrieval over the seed corpus - not a stand-in to
be swapped later, the search algorithm itself is genuine dense + sparse + fused
ranking. Only the document *set* is a small fixture (`app/mock/documents.py`); point
`HybridRagStore` at a different `documents` list (same dict shape as the example in
the prompt this project started from) and everything else is unchanged.

**Dense** (`app/rag/embeddings.py`) — local ONNX embeddings via
[fastembed](https://github.com/qdrant/fastembed) (`BAAI/bge-small-en-v1.5`, 384-dim).
Chosen over the KodeKloud gateway because that gateway has no embeddings endpoint for
this project's key (every model returns 403/400 on `/embeddings` — confirmed against
the live gateway). Local also means no per-query cost or latency to an external API.
Runs on CPU via `asyncio.to_thread`, so it never blocks the event loop. Model weights
download once (~130MB) on first use and are cached to disk by fastembed; every
`search()` after that is offline.

**Sparse** (`app/rag/sparse.py`) — classic BM25 via `rank_bm25`, tokenized with the
same stopword-stripped word tokenizer the mock search used to use directly.

**Fusion** (`app/rag/store.py::HybridRagStore.search`) —

1. Pull the top `HYBRID_CANDIDATE_K` candidates from each method independently
   (restricted to the requested `document_type` *before* the top-K cut, not after —
   filtering after would let a document_type filter silently starve a search of a
   good match that only missed the global top-K).
2. Take the **union** of both candidate sets - a document that only one method rated
   highly is still eligible, not excluded for missing the other's cut.
3. Min-max normalize each method's scores across that union into `[0, 1]` (cosine
   similarity and raw BM25 scores live on incomparable scales, so combining them
   raw would let whichever happens to have the larger numbers dominate regardless
   of actual relevance).
4. `hybrid_score = HYBRID_ALPHA * dense_norm + (1 - HYBRID_ALPHA) * sparse_norm`,
   sorted descending, top `limit` returned.

`document_search` returns `score`, `dense_score` and `sparse_score` for every result
— visible to the agent's reasoning and streamed to the Agent Activity Panel as a
`retrieval_status` event, so the balance between semantic and keyword matching is
never a black box.

**Verified on the live corpus**, not just asserted in tests — a genuinely paraphrased
query ("How do we keep card data safe?", sharing almost no vocabulary with the
target document) correctly surfaced the Payment Security Policy and related policies
via the dense signal, with both scores visible throughout.

**Degrades gracefully.** If the embedding model can't be loaded (no network on a
cold cache, package issue), `search()` falls back to sparse-only rather than
failing the request — the same "answer with what's available" posture as MCP-tool
and LLM-model-check failures elsewhere in this app. `GET /health` reports
`dense_search_available`; the corpus is embedded once at startup
(`lifespan` → `warm_up()`) so the first real request never pays that latency.

**Configuration** (`.env.example`):

| Setting | Default | Meaning |
|---|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Any fastembed-supported model name |
| `HYBRID_CANDIDATE_K` | `20` | Candidates pulled from each method before fusion |
| `HYBRID_ALPHA` | `0.5` | Fusion weight — `1.0` = pure dense, `0.0` = pure BM25 |

Tests never load the real model — `tests/test_rag.py` injects a small deterministic
fake embedder to exercise the fusion math, and a session-scoped fixture
(`tests/conftest.py`) disables dense search on the shared store for every other
test, so the full suite stays offline and fast (BM25 alone is enough to exercise the
tool layer).
