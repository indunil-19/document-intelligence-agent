# Internal Document Intelligence API

An async FastAPI chat endpoint backed by a three-agent LangGraph pipeline over internal
company documents (policies, architecture docs, runbooks, incident reports, product
specs, meeting notes).

The RAG layer is mocked — `app/mock/rag.py` is a small async facade over a seed corpus.
Replace its four methods with the real vector store and nothing else changes.

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
the out-of-scope and clarification cases.

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
| `GET /health` | Status, model, gateway URL, whether MCP is connected, known document types |
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

## Configuration

All settings are environment variables (see `.env.example`). `MODEL` and
`SUBAGENT_MODEL` both default to `gpt-oss-120b` — confirm against
`python -m scripts.list_models`, since the gateway decides what is available. Set
`SUBAGENT_MODEL` to a cheaper model if analytics fan-outs get expensive.
`ENABLE_MCP=false` runs without the directory tools.

## Tests

```bash
pytest
```

20 tests, no API key or network needed — LLM calls are stubbed. They cover the tools
and their failure paths, graph routing (retrieval / direct / out-of-scope), evidence
and source propagation, degradation when the orchestrator or retrieval fails, and the
analytics inline/fan-out split including partial and total sub-agent failure.

## Layout

```
app/
  main.py              FastAPI app, exception handlers, correlation middleware
  chat_service.py      Session handling + graph invocation
  config.py            Settings
  logging_config.py    JSON logging with request/session context
  errors.py            Domain exceptions
  schemas.py           Request/response models
  llm.py               Chat model factory + gateway model check
  session.py           In-memory session + document cache
  agents/
    loader.py          Loads instruction files
    instructions/      orchestrator.md · retrieval.md · response.md
  graph/
    builder.py         Graph wiring
    state.py           Shared state
    nodes/             orchestrator.py · retrieval.py · response.py
  tools/
    rag_tools.py       The four RAG tools + analytics fan-out
    mcp_tools.py       MCP client loading, degrades if unavailable
  mcp_server/
    server.py          Stdio MCP server (employee directory, service catalog)
  mock/
    rag.py             Mock RAG store — replace with the real one
    mock_api.py        Mock REST API the MCP tools call
    documents.py       Seed corpus
tests/
```

## Replacing the mock RAG

Implement `search`, `metadata_for_type`, `filter_by_metadata` and `get_many` against the
real store and return the same dict shapes from `app/mock/rag.py`. The tools, agents
and graph are unchanged.
