"""Streamlit chat frontend for the Internal Document Intelligence API.

Run:
    streamlit run frontend/app.py

Talks to the FastAPI backend over plain HTTP (requests) - /auth/login, /session and
/chat/stream. Nothing here calls an LLM or touches the graph directly; this is a
thin, transparent view over what the backend is already doing, which is the point:
the Agent Activity Panel exists so an evaluator can watch the real internal state
machine (LangGraph nodes, tool calls, memory writes, validation checks) rather than
a hidden black box behind a single "thinking..." spinner.
"""
import json
import os

import requests
import streamlit as st

BACKEND_URL_DEFAULT = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = 180

DEMO_CREDENTIALS = [
    ("viewer", "viewer123", "document_search"),
    ("analyst", "analyst123", "document_search, metadata_retrieval, filter_by_metadata, analyze_documents"),
    ("admin", "admin123", "all of the above, plus employee_directory, service_catalog"),
]

# Icons for the activity log. `token` is deliberately absent - individual answer
# chunks aren't logged as steps, they go straight into the chat bubble.
ICONS = {
    "node_start": "▶️",     # ▶️
    "node_end": "✅",             # ✅
    "tool_start": "\U0001f527",       # 🔧
    "tool_end": "\U0001f527",         # 🔧
    "tool_error": "❌",           # ❌
    "retrieval_status": "\U0001f4e1", # 📡
    "memory_update": "\U0001f4be",    # 💾
    "validation": "\U0001f9ea",       # 🧪
    "final": "\U0001f3c1",            # 🏁
    "error": "\U0001f6d1",            # 🛑
}

STATUS_ICON = {"running": "⏳", "complete": "✅", "error": "\U0001f6d1"}


# --- session state --------------------------------------------------------------


def _init_state() -> None:
    defaults = {
        "authenticated": False,
        "username": None,
        "role": None,
        "display_name": None,
        "session_id": None,
        "messages": [],   # [{"role": "user"/"assistant", "content": str}]
        "turns": [],       # [{"question": str, "events": [dict], "status": str}]
        "backend_url": BACKEND_URL_DEFAULT,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.turns = []
    st.session_state.session_id = None


def _log_out() -> None:
    for key in ("authenticated", "username", "role", "display_name"):
        st.session_state[key] = None
    st.session_state.authenticated = False
    _reset_conversation()


# --- backend calls ---------------------------------------------------------------


def _login(username: str, password: str) -> dict | None:
    try:
        response = requests.post(
            f"{st.session_state.backend_url}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
    except requests.RequestException as exc:
        st.error(f"Could not reach the backend: {exc}")
        return None

    if response.status_code == 200:
        return response.json()
    try:
        detail = response.json().get("message", response.text)
    except ValueError:
        detail = response.text
    st.error(f"Login failed: {detail}")
    return None


def _create_session() -> str | None:
    try:
        response = requests.post(
            f"{st.session_state.backend_url}/session", timeout=10
        )
        response.raise_for_status()
        return response.json()["session_id"]
    except requests.RequestException as exc:
        st.error(f"Could not create a session: {exc}")
        return None


def _stream_chat(message: str):
    """Yields parsed NDJSON events from /chat/stream, or a single synthetic
    "error" event if the request fails outright (network error, non-streaming
    error response such as a 429 from the rate limiter, which arrives as a plain
    JSON body rather than NDJSON since it never reaches stream_chat)."""
    try:
        response = requests.post(
            f"{st.session_state.backend_url}/chat/stream",
            json={"message": message, "session_id": st.session_state.session_id},
            headers={"X-User-Id": st.session_state.username},
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        yield {"type": "error", "message": f"Could not reach the backend: {exc}", "data": {}}
        return

    if response.status_code != 200:
        # Rejected before streaming started (e.g. 429 rate limited, 422 invalid
        # body) - a normal single-body JSON error, not our NDJSON framing.
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text}
        yield {"type": "error", "message": body.get("message", "Request failed"), "data": body}
        return

    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        try:
            yield json.loads(raw_line)
        except json.JSONDecodeError:
            continue


# --- rendering -------------------------------------------------------------------


def _format_event(event: dict) -> str:
    icon = ICONS.get(event["type"], "•")
    label = event.get("node") or event.get("tool") or ""
    prefix = f"**{label}** — " if label else ""
    return f"{icon} {prefix}{event.get('message', '')}"


def _render_turn_body(events: list[dict]) -> None:
    for event in events:
        if event["type"] == "token":
            continue  # answer text, not a step - shown in the chat bubble instead
        st.markdown(_format_event(event))


def _render_history() -> None:
    for turn in st.session_state.turns:
        icon = STATUS_ICON.get(turn["status"], "•")
        with st.expander(f"{icon} {turn['question'][:70]}", expanded=False):
            _render_turn_body(turn["events"])


def _run_turn(prompt: str, chat_col, activity_col) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with chat_col:
        st.chat_message("user").write(prompt)
        assistant_placeholder = st.chat_message("assistant").empty()

    turn = {"question": prompt, "events": [], "status": "running"}

    with activity_col:
        status = st.status("Starting…", expanded=True)

    answer = ""
    final_payload: dict | None = None
    with status:
        for event in _stream_chat(prompt):
            turn["events"].append(event)
            event_type = event["type"]

            if event_type == "token":
                answer += event.get("data", {}).get("text", "")
                assistant_placeholder.markdown(answer + "▌")
                continue

            # Every non-token event both narrates a step and updates the headline
            # "current agent state" label - the backend's message text is the
            # single source of truth for both, so the two never drift apart.
            st.write(_format_event(event))
            status.update(label=event.get("message") or event_type)

            if event_type == "final":
                final_payload = event["data"]
                status.update(label="Response complete", state="complete")
            elif event_type == "error":
                status.update(label=f"Failed: {event.get('message')}", state="error")

    if final_payload is not None:
        answer = final_payload.get("answer", answer)
        turn["status"] = "complete"
        assistant_placeholder.markdown(answer)
        if final_payload.get("session_id"):
            st.session_state.session_id = final_payload["session_id"]
        if final_payload.get("sources"):
            with chat_col:
                names = ", ".join(
                    f"{s['document_id']} ({s['title']})" for s in final_payload["sources"]
                )
                st.caption(f"Sources: {names}")
        if final_payload.get("degraded"):
            with chat_col:
                st.caption("⚠️ This answer was produced with incomplete evidence.")
    else:
        turn["status"] = "error"
        error_events = [e for e in turn["events"] if e["type"] == "error"]
        answer = (error_events[-1]["message"] if error_events else "Something went wrong.")
        assistant_placeholder.markdown(f"⚠️ {answer}")

    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.session_state.turns.append(turn)


# --- sidebar ---------------------------------------------------------------------


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Internal Doc Assistant")
        st.session_state.backend_url = st.text_input(
            "Backend URL", value=st.session_state.backend_url
        )

        if not st.session_state.authenticated:
            st.subheader("Log in")
            with st.form("login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Log in")
            if submitted:
                result = _login(username, password)
                if result is not None:
                    st.session_state.authenticated = True
                    st.session_state.username = result["username"]
                    st.session_state.role = result["role"]
                    st.session_state.display_name = result["display_name"]
                    st.session_state.session_id = _create_session()
                    st.rerun()

            with st.expander("Demo credentials", expanded=True):
                for username, password, tools in DEMO_CREDENTIALS:
                    st.markdown(f"**{username}** / `{password}`\n\n_{tools}_")
        else:
            st.success(f"{st.session_state.display_name}")
            st.caption(f"Role: `{st.session_state.role}`")
            st.caption(f"Session ID: `{st.session_state.session_id}`")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("New chat", use_container_width=True):
                    _reset_conversation()
                    st.session_state.session_id = _create_session()
                    st.rerun()
            with col2:
                if st.button("Log out", use_container_width=True):
                    _log_out()
                    st.rerun()


# --- main -------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Internal Doc Assistant", layout="wide")
    _init_state()
    _render_sidebar()

    st.title("Internal Document Intelligence Chat")
    st.caption(
        "Chat Window on the left, Agent Activity Panel on the right - every "
        "LangGraph node, tool call, memory write and validation check is shown as "
        "it happens."
    )

    if not st.session_state.authenticated:
        st.info("Log in from the sidebar to start chatting. Three demo accounts are available.")
        return

    # Read this before laying out the columns, even though it renders pinned to the
    # bottom of the page regardless of call order - that lets the placeholder below
    # know a new turn is about to run in this same script execution, instead of
    # flashing "no activity yet" right above the turn it's about to render.
    prompt = st.chat_input("Ask about company documents…")

    chat_col, activity_col = st.columns([2, 1])

    with chat_col:
        st.subheader("Chat")
        for message in st.session_state.messages:
            st.chat_message(message["role"]).write(message["content"])

    with activity_col:
        st.subheader("Agent Activity")
        if not st.session_state.turns and not prompt:
            st.caption("No activity yet — ask a question to see the agent work in real time.")
        _render_history()

    if prompt:
        _run_turn(prompt, chat_col, activity_col)


if __name__ == "__main__":
    main()
