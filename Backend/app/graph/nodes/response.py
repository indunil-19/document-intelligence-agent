"""Response generation agent: turns the evidence brief into the user-facing answer.

Streams the answer token-by-token via llm.astream() rather than one ainvoke() call,
so the Streamlit chat window can render it incrementally instead of waiting for the
whole response.
"""
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.activity import emit
from app.agents.loader import load_instructions
from app.errors import LLMError
from app.graph.state import ChatState
from app.llm import get_llm

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = (
    "I could not reach the document store just now, so I do not have a grounded "
    "answer for that. Please try again in a moment."
)


def _build_prompt(state: ChatState) -> str:
    parts = [f"User question:\n{state['question']}"]

    if state.get("history"):
        recent = "\n".join(f"{t['role']}: {t['content']}" for t in state["history"][-6:])
        parts.append(f"Recent conversation:\n{recent}")

    parts.append(f"Interpreted intent: {state.get('intent', 'unknown')}")

    route = state.get("route")
    if route == "out_of_scope":
        guidance = state.get("refusal_guidance") or (
            "The request falls outside the internal document set this assistant covers."
        )
        parts.append(f"This request is OUT OF SCOPE. Guidance for declining:\n{guidance}")
    elif route == "direct":
        parts.append(
            "No document retrieval was needed. Answer conversationally and, if the user "
            "asked what you can do, describe the internal documentation you cover: "
            "policies, architecture documents, runbooks, incident reports, product specs "
            "and meeting notes, plus employee and service ownership lookups."
        )

    evidence = state.get("evidence")
    if evidence:
        parts.append(f"Evidence brief from the retrieval agent:\n{evidence}")
    elif route == "retrieval":
        if state.get("notes"):
            parts.append(
                "The search did not finish: "
                + "; ".join(state["notes"])
                + ".\nTell the user you were not able to complete the lookup and "
                "suggest they narrow the question or try again. Do NOT say the "
                "information does not exist — you do not know that."
            )
        else:
            parts.append(
                "No evidence was gathered — retrieval returned nothing. Tell the user "
                "plainly that you could not find anything covering this, and do not "
                "invent an answer."
            )

    if state.get("clarification"):
        parts.append(f"Clarifying question to ask at the end:\n{state['clarification']}")

    return "\n\n".join(parts)


async def response_node(state: ChatState) -> ChatState:
    emit("node_start", node="respond", message="Generating final response")
    llm = get_llm()
    answer = ""
    first_token = True
    try:
        async for chunk in llm.astream(
            [
                SystemMessage(content=load_instructions("response")),
                HumanMessage(content=_build_prompt(state)),
            ]
        ):
            text = chunk.text
            if not text:
                continue
            if first_token:
                emit("retrieval_status", node="respond", message="Streaming response…")
                first_token = False
            answer += text
            emit("token", node="respond", text=text)
        answer = answer.strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("response generation failed", extra={"event": "node.response.error"})
        emit("node_end", node="respond", message="Response generation failed")
        raise LLMError("response generation failed", details={"reason": str(exc)}) from exc

    if not answer:
        logger.warning("empty answer from model", extra={"event": "node.response.empty"})
        answer = FALLBACK_ANSWER
        emit("token", node="respond", text=answer)

    logger.info(
        "response generated",
        extra={"event": "node.response", "answer_chars": len(answer)},
    )
    emit("node_end", node="respond", message="Response complete", answer_chars=len(answer))
    return {"answer": answer}
