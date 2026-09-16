"""Graph wiring and routing, with the three agents' LLM calls stubbed out."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.builder import build_graph
from app.graph.nodes.orchestrator import OrchestratorDecision
from app.graph.nodes.retrieval import STEP_LIMIT_SENTINEL


class FakeStructuredLLM:
    def __init__(self, decision):
        self._decision = decision

    async def ainvoke(self, _messages):
        return self._decision


class _Chunk:
    """Minimal stand-in for an AIMessageChunk - just the .text property."""

    def __init__(self, text):
        self.text = text


class FakeLLM:
    """Stands in for ChatOpenAI. Records the prompt it was handed."""

    def __init__(self, decision=None, text="stub answer"):
        self._decision = decision
        self._text = text
        self.last_prompt = None

    def with_structured_output(self, _schema):
        return FakeStructuredLLM(self._decision)

    async def ainvoke(self, messages):
        self.last_prompt = messages[-1].content
        return AIMessage(content=self._text)

    async def astream(self, messages):
        self.last_prompt = messages[-1].content
        yield _Chunk(self._text)


class FakeRetrievalAgent:
    def __init__(self, messages):
        self._messages = messages
        self.invoked_with = None

    async def ainvoke(self, payload, config=None):
        self.invoked_with = (payload, config)
        return {"messages": self._messages}


def _decision(**overrides):
    base = dict(
        intent="Find what the payment security policy requires.",
        intent_type="lookup",
        route="retrieval",
        in_scope=True,
        document_types=["policy"],
        metadata_filters={},
        clarification=None,
        refusal_guidance=None,
    )
    base.update(overrides)
    return OrchestratorDecision(**base)


@pytest.fixture
def patched(monkeypatch):
    """Patch each node's LLM entry point; returns the fakes for assertions."""

    def install(decision, retrieval_messages=None, answer="stub answer"):
        orchestrator_llm = FakeLLM(decision=decision)
        response_llm = FakeLLM(text=answer)
        agent = FakeRetrievalAgent(retrieval_messages or [])

        monkeypatch.setattr(
            "app.graph.nodes.orchestrator.get_llm", lambda: orchestrator_llm
        )
        monkeypatch.setattr("app.graph.nodes.response.get_llm", lambda: response_llm)
        # get_retrieval_agent(role) is looked up per-role; every role gets the
        # same fake here since these tests don't exercise role-based tool access.
        monkeypatch.setattr(
            "app.graph.nodes.retrieval.get_retrieval_agent", lambda role: agent
        )
        return orchestrator_llm, response_llm, agent

    return install


BASE_STATE = {
    "session_id": "test-session",
    "question": "What does the payment security policy require?",
    "history": [],
    "role": "viewer",
    "degraded": False,
    "notes": [],
}


async def test_retrieval_route_collects_evidence_and_sources(patched):
    messages = [
        HumanMessage(content="task"),
        ToolMessage(
            content='{"documents": [{"document_id": "POL-001"}, {"document_id": "ARC-001"}]}',
            name="document_search",
            tool_call_id="1",
        ),
        AIMessage(content="POL-001 requires encryption in transit and at rest."),
    ]
    _, response_llm, agent = patched(_decision(), retrieval_messages=messages)

    result = await build_graph().ainvoke(BASE_STATE)

    assert result["route"] == "retrieval"
    assert result["answer"] == "stub answer"
    assert result["tools_used"] == ["document_search"]
    assert {s["document_id"] for s in result["sources"]} == {"POL-001", "ARC-001"}
    # Evidence must actually reach the response agent.
    assert "encryption in transit" in response_llm.last_prompt
    # Session id must reach the tools through the agent config.
    assert agent.invoked_with[1]["configurable"]["session_id"] == "test-session"


async def test_out_of_scope_skips_retrieval(patched):
    _, response_llm, agent = patched(
        _decision(
            route="out_of_scope",
            intent_type="out_of_scope",
            in_scope=False,
            refusal_guidance="Say it is outside internal documentation.",
        ),
        answer="That is outside what I cover.",
    )

    result = await build_graph().ainvoke(
        {**BASE_STATE, "question": "What is the weather in Paris?"}
    )

    assert result["route"] == "out_of_scope"
    assert result["in_scope"] is False
    assert agent.invoked_with is None  # retrieval never ran
    assert "OUT OF SCOPE" in response_llm.last_prompt
    assert "outside internal documentation" in response_llm.last_prompt


async def test_direct_route_skips_retrieval(patched):
    _, _, agent = patched(_decision(route="direct", intent_type="chitchat"))
    result = await build_graph().ainvoke({**BASE_STATE, "question": "hello"})
    assert result["route"] == "direct"
    assert agent.invoked_with is None


async def test_clarification_is_passed_to_the_response_agent(patched):
    _, response_llm, _ = patched(
        _decision(clarification="Which policy do you mean?"),
        retrieval_messages=[AIMessage(content="Several policies exist.")],
    )
    await build_graph().ainvoke({**BASE_STATE, "question": "tell me about the policy"})
    assert "Which policy do you mean?" in response_llm.last_prompt


async def test_failed_retrieval_degrades_instead_of_erroring(patched, monkeypatch):
    _, response_llm, _ = patched(_decision())

    class ExplodingAgent:
        async def ainvoke(self, payload, config=None):
            raise RuntimeError("rag is down")

    monkeypatch.setattr(
        "app.graph.nodes.retrieval.get_retrieval_agent", lambda role: ExplodingAgent()
    )

    result = await build_graph().ainvoke(BASE_STATE)

    assert result["degraded"] is True
    assert result["answer"] == "stub answer"  # user still gets a reply
    # A crashed search is an incomplete search, not proof the information is absent.
    assert "The search did not finish" in response_llm.last_prompt
    assert "rag is down" in response_llm.last_prompt
    assert "does not exist" in response_llm.last_prompt


async def test_orchestrator_failure_falls_back_to_retrieval(monkeypatch):
    class ExplodingLLM:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr("app.graph.nodes.orchestrator.get_llm", lambda: ExplodingLLM())
    monkeypatch.setattr("app.graph.nodes.response.get_llm", lambda: FakeLLM(text="ok"))
    monkeypatch.setattr(
        "app.graph.nodes.retrieval.get_retrieval_agent",
        lambda role: FakeRetrievalAgent([AIMessage(content="evidence")]),
    )

    result = await build_graph().ainvoke(BASE_STATE)

    assert result["route"] == "retrieval"
    assert result["degraded"] is True
    assert result["answer"] == "ok"


async def test_step_limit_cutoff_is_not_reported_as_evidence(patched):
    """The agent's out-of-steps message must not become an "I found nothing" answer."""
    messages = [
        ToolMessage(
            content='{"results": [{"service_id": "SVC-PAY"}]}',
            name="service_catalog",
            tool_call_id="1",
        ),
        AIMessage(content=STEP_LIMIT_SENTINEL),
    ]
    _, response_llm, _ = patched(_decision(), retrieval_messages=messages)

    result = await build_graph().ainvoke(BASE_STATE)

    assert result["evidence"] == ""            # sentinel discarded
    assert result["degraded"] is True
    assert "step budget exhausted" in " ".join(result["notes"])
    # The response agent is told not to claim the information is absent.
    assert "did not finish" in response_llm.last_prompt
    assert "does not exist" in response_llm.last_prompt
    assert STEP_LIMIT_SENTINEL not in response_llm.last_prompt


async def test_role_defaults_to_viewer_when_absent(patched):
    """retrieval_node must not KeyError when role is missing from state."""
    _, _, agent = patched(_decision(), retrieval_messages=[AIMessage(content="ok")])
    state = {k: v for k, v in BASE_STATE.items() if k != "role"}

    result = await build_graph().ainvoke(state)

    assert result["degraded"] is False
