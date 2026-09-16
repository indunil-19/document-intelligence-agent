"""Analytics tool: inline vs. sub-agent fan-out. Sub-agent LLM calls are stubbed."""
import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.session import get_session_store
from app.tools.rag_tools import analyze_documents, filter_by_metadata


class RecordingLLM:
    """Counts calls and records concurrency so fan-out can be asserted."""

    def __init__(self):
        self.calls = []
        self.concurrent = 0
        self.peak_concurrent = 0

    async def ainvoke(self, messages):
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        self.calls.append(messages[-1].content)
        await asyncio.sleep(0.01)
        self.concurrent -= 1
        return AIMessage(content="finding [POL-001]")


@pytest.fixture
def stub_llm(monkeypatch):
    llm = RecordingLLM()
    monkeypatch.setattr("app.tools.rag_tools.get_subagent_llm", lambda: llm)
    return llm


@pytest.fixture
async def config():
    session = await get_session_store().get_or_create(None)
    return {"configurable": {"session_id": session.session_id}}


async def test_small_set_is_analysed_inline(stub_llm, config):
    await filter_by_metadata.ainvoke(
        {"filters": {"document_type": "incident_report"}}, config=config
    )
    result = await analyze_documents.ainvoke(
        {"instruction": "list the root causes"}, config=config
    )

    assert result["mode"] == "inline"
    assert result["document_count"] == 3
    assert len(stub_llm.calls) == 1  # no fan-out, no merge
    assert "list the root causes" in stub_llm.calls[0]


async def test_large_set_fans_out_to_parallel_subagents(stub_llm, config):
    # 13 Engineering documents, above the default threshold of 10.
    filtered = await filter_by_metadata.ainvoke(
        {"filters": {"department": "Engineering"}}, config=config
    )
    assert filtered["count"] > 10

    result = await analyze_documents.ainvoke(
        {"instruction": "find recurring themes"}, config=config
    )

    assert result["mode"] == "fanout"
    assert result["subagents"] == 3  # 13 documents in batches of 5
    assert result["failed_subagents"] == 0
    assert result["truncated"] is False
    # 3 batch calls plus 1 merge call, and the batches ran concurrently.
    assert len(stub_llm.calls) == 4
    assert stub_llm.peak_concurrent == 3


async def test_fanout_survives_a_failing_subagent(monkeypatch, config):
    calls = {"n": 0}

    class FlakyLLM:
        async def ainvoke(self, messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("sub-agent exploded")
            return AIMessage(content="partial finding")

    monkeypatch.setattr("app.tools.rag_tools.get_subagent_llm", lambda: FlakyLLM())

    await filter_by_metadata.ainvoke(
        {"filters": {"department": "Engineering"}}, config=config
    )
    result = await analyze_documents.ainvoke({"instruction": "x"}, config=config)

    assert result["mode"] == "fanout"
    assert result["failed_subagents"] == 1
    assert result["analysis"]  # the surviving batches still produce a result


async def test_fanout_reports_when_every_subagent_fails(monkeypatch, config):
    class DeadLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("all down")

    monkeypatch.setattr("app.tools.rag_tools.get_subagent_llm", lambda: DeadLLM())

    await filter_by_metadata.ainvoke(
        {"filters": {"department": "Engineering"}}, config=config
    )
    result = await analyze_documents.ainvoke({"instruction": "x"}, config=config)

    assert "error" in result
