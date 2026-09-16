"""Tools and the mock store. No LLM calls."""
import pytest

from app.session import get_session_store
from app.tools.rag_tools import (
    analyze_documents,
    document_search,
    filter_by_metadata,
    metadata_retrieval,
)


@pytest.fixture
async def session_config():
    session = await get_session_store().get_or_create(None)
    return session, {"configurable": {"session_id": session.session_id}}


async def test_document_search_ranks_relevant_document_first():
    result = await document_search.ainvoke(
        {"query": "payment credentials encryption storage"}
    )
    assert result["count"] > 0
    assert result["documents"][0]["document_id"] == "POL-001"


async def test_document_search_narrows_by_type():
    result = await document_search.ainvoke(
        {"query": "timeout", "document_type": "runbook"}
    )
    assert all(d["document_type"] == "runbook" for d in result["documents"])


async def test_document_search_rejects_unknown_type():
    result = await document_search.ainvoke({"query": "x", "document_type": "memo"})
    assert "error" in result
    assert "policy" in result["valid_document_types"]


async def test_metadata_retrieval_reports_fields_and_values():
    result = await metadata_retrieval.ainvoke({"document_type": "incident_report"})
    assert result["document_count"] == 3
    assert "severity" in result["fields"]
    assert "sev1" in result["fields"]["severity"]


async def test_filter_by_metadata_counts_and_caches(session_config):
    session, config = session_config
    result = await filter_by_metadata.ainvoke(
        {"filters": {"document_type": "policy", "owner": "Security Team"}}, config=config
    )
    assert result["count"] == 2
    assert result["cached_for_analysis"] is True
    # Contents are cached for analysis but not returned to the agent.
    assert "content" not in result["documents"][0]
    assert len(session.cached_documents()) == 2


async def test_filter_by_metadata_is_case_insensitive(session_config):
    _, config = session_config
    result = await filter_by_metadata.ainvoke(
        {"filters": {"document_type": "POLICY", "status": "Active"}}, config=config
    )
    assert result["count"] == 2


async def test_filter_rejects_empty_filters(session_config):
    _, config = session_config
    result = await filter_by_metadata.ainvoke({"filters": {}}, config=config)
    assert "error" in result


async def test_analytics_without_cached_documents_explains_itself(session_config):
    _, config = session_config
    result = await analyze_documents.ainvoke({"instruction": "summarise"}, config=config)
    assert "error" in result
    assert "filter_by_metadata" in result["hint"]


async def test_analytics_without_session_does_not_raise():
    result = await analyze_documents.ainvoke(
        {"instruction": "summarise"}, config={"configurable": {"session_id": "missing"}}
    )
    assert "error" in result
