import os
import uuid
import json
from pathlib import Path
import pandas as pd
import hmac

import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from langchain_core.messages import HumanMessage, ToolMessage


# 1. PROJECT SETUP
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Assumes UI.py is in the project root and this module exports fde_agent.
from orchestrator import fde_agent


# 2. PAGE CONFIGURATION
st.set_page_config(
    page_title="Cold Chain Logistics Agent",
    page_icon="🧊",
    layout="wide",
)

st.markdown(
    """
    <style>
        .stApp { background-color: #0B0E14; color: #E2E8F0; }
        section[data-testid="stSidebar"] { background-color: #111622; }
        .stMarkdown code {
            background-color: #1E293B;
            color: #38BDF8;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# 3. MYSQL AUDIT LOGGING
@st.cache_resource(show_spinner=False)
def get_audit_engine():
    """Create and cache the SQLAlchemy MySQL connection pool."""
    db_url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["AGENT_DB_USER"],
        password=os.environ["AGENT_DB_PASSWORD"],
        host=os.getenv("AGENT_DB_HOST", "localhost"),
        port=int(os.getenv("AGENT_DB_PORT", "3306")),
        database=os.environ["AGENT_DB_NAME"],
    )
    return create_engine(db_url, pool_pre_ping=True, pool_recycle=1800)
@st.cache_resource(show_spinner=False)
def get_audit_read_engine():

    db_url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["AUDIT_DB_USER"],
        password=os.environ["AUDIT_DB_PASSWORD"],
        host=os.getenv("AUDIT_DB_HOST", "localhost"),
        port=int(os.getenv("AUDIT_DB_PORT", "3306")),
        database=os.environ["AGENT_DB_NAME"],
    )

    return create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=1800
    )

def write_audit_log(session_id, node_name, tool_name, content):
    """Insert one agent event into fde_views.AgentAuditLog."""
    try:
        with get_audit_engine().begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO fde_views.AgentAuditLog
                        (SessionID, NodeExecuted, ToolName, Content)
                    VALUES
                        (:session_id, :node_name, :tool_name, :content)
                """),
                {
                    "session_id": session_id,
                    "node_name": node_name,
                    "tool_name": tool_name,
                    "content": str(content)[:60000],
                },
            )
    except Exception as exc:
        # Audit failure should not prevent the agent from answering.
        print(f"Audit logging failed: {exc}")
def show_audit_dashboard():

    st.title("🛡️ Agent Audit Dashboard")

    st.caption(
        "Monitor agent execution, tool activity and conversation history."
    )

    # Session ID filter
    session_filter = st.text_input(
        "Filter by Session ID",
        placeholder="Enter a session ID or leave empty"
    )

    try:
        with get_audit_read_engine().connect() as conn:

            query = """
                SELECT
                    SessionID,
                    NodeExecuted,
                    ToolName,
                    Content
                FROM fde_views.AgentAuditLog
            """

            params = {}

            if session_filter.strip():
                query += " WHERE SessionID = :session_id"
                params["session_id"] = session_filter.strip()

            query += " LIMIT 500"

            result = conn.execute(
                text(query),
                params
            )

            records = result.mappings().all()

            df = pd.DataFrame(records)

            if df.empty:
                st.info("No audit records found.")

            else:

                # Metrics
                col1, col2, col3 = st.columns(3)

                col1.metric("Total Events", len(df))

                col2.metric(
                    "Tool Executions",
                    (df["NodeExecuted"] == "tools").sum()
                )

                col3.metric(
                    "Unique Sessions",
                    df["SessionID"].nunique()
                )

                st.divider()

                # Audit records
                st.subheader("Audit Records")

                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                # Detailed inspection
                st.subheader("Inspect Individual Event")

                selected_index = st.selectbox(
                    "Select an audit record",
                    range(len(df)),
                    format_func=lambda i: (
                        f"{df.iloc[i]['NodeExecuted']} | "
                        f"{df.iloc[i]['ToolName']} | "
                        f"{df.iloc[i]['SessionID']}"
                    )
                )

                selected_record = df.iloc[selected_index]

                st.json({
                    "SessionID": selected_record["SessionID"],
                    "NodeExecuted": selected_record["NodeExecuted"],
                    "ToolName": selected_record["ToolName"],
                    "Content": selected_record["Content"]
                })

    except Exception as exc:
        st.error(f"Unable to load audit records: {exc}")
def authenticate_admin():

    if "audit_authenticated" not in st.session_state:
        st.session_state.audit_authenticated = False

    if st.session_state.audit_authenticated:
        return True

    st.title("Admin Authentication")
    st.caption("Restricted access to agent audit records.")

    password = st.text_input(
        "Admin Password",
        type="password"
    )

    if st.button("Login", use_container_width=True):

        expected_password = os.environ["AUDIT_ADMIN_PASSWORD"]

        if hmac.compare_digest(password, expected_password):
            st.session_state.audit_authenticated = True
            st.rerun()
        else:
            st.error("Invalid admin password.")

    return st.session_state.audit_authenticated

# 4. SESSION STATE
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

thread_config = {
    "configurable": {"thread_id": st.session_state.thread_id}
}


# 5. SIDEBAR
with st.sidebar:
    st.title("🧊 Cold Chain FDE")
    st.caption("Logistics Intelligence Agent")
    page = st.radio(
        "Navigation",
        ["Chat Assistant", "Audit Dashboard"]
    )
    st.divider()
    
    st.caption("SESSION")
    st.code(st.session_state.thread_id[:8] + "...")

    if st.button("＋ New conversation", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()
    

if page == "Audit Dashboard":

    if authenticate_admin():

        st.sidebar.success("Admin authenticated")

        if st.sidebar.button("Logout"):
            st.session_state.audit_authenticated = False
            st.rerun()

        show_audit_dashboard()

    st.stop()


st.title("Cold Chain Logistics Assistant")
st.caption(
    "Ask about fleet telemetry, corridor weather, delivery risks, "
    "and cold-chain compliance."
)

# Render conversation history.
for entry in st.session_state.messages:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])

        for trace in entry.get("traces", []):
            with st.expander(f"{trace['type']}: {trace['name']}"):
                if trace["type"] == "Tool input":
                    st.json(trace["content"])
                else:
                    st.code(trace["content"], language="text")


# Handle a new question.
if user_input := st.chat_input("Ask your logistics question..."):

    st.session_state.messages.append(
        {"role": "user", "content": user_input}
    )

    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        final_response = ""
        traces = []

        try:
            
            with st.status("Agent is processing...", expanded=True) as status:

                st.write("Starting agent workflow...")

                events = fde_agent.stream(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=thread_config,
                    stream_mode="updates",
                )

                for event in events:
                    for node_name, node_state in event.items():

                        if node_name == "reasoner":

                            st.write("Reasoner is processing...")

                            node_messages = node_state.get("messages", [])
                            if not node_messages:
                                continue

                            latest_msg = node_messages[-1]
                            tool_calls = getattr(latest_msg, "tool_calls", [])

                            for tool_call in tool_calls:

                                tool_name = tool_call["name"]
                                tool_args = tool_call.get("args", {})

                                st.write(f"Calling tool: **{tool_name}**")

                                with st.expander(f"Tool input: {tool_name}"):
                                    st.json(tool_args)

                                traces.append({
                                    "type": "Tool input",
                                    "name": tool_name,
                                    "content": tool_args,
                                })

                                write_audit_log(
                                    st.session_state.thread_id,
                                    "reasoner",
                                    tool_name,
                                    json.dumps(tool_args, default=str),
                                )

                            if latest_msg.content and not tool_calls:

                                final_response = str(latest_msg.content)

                                st.write("Final response generated.")

                                write_audit_log(
                                    st.session_state.thread_id,
                                    "reasoner",
                                    "Final response",
                                    final_response,
                                )

                        elif node_name == "tools":

                            for tool_msg in node_state.get("messages", []):

                                if not isinstance(tool_msg, ToolMessage):
                                    continue

                                tool_name = tool_msg.name or "unknown_tool"
                                tool_output = str(tool_msg.content)

                                st.write(f"Tool executed: **{tool_name}**")

                                with st.expander(f"Tool output: {tool_name}"):
                                    st.code(tool_output[:5000], language="text")

                                traces.append({
                                    "type": "Tool output",
                                    "name": tool_name,
                                    "content": tool_output,
                                })

                                write_audit_log(
                                    st.session_state.thread_id,
                                    "tools",
                                    tool_name,
                                    tool_output,
                                )

                status.update(
                    label="Request completed",
                    state="complete",
                    expanded=False,
                )


            if final_response:
                st.markdown(final_response)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": final_response,
                    "traces": traces,
                })
            else:
                st.warning("The agent did not return a final response.")

        except Exception as exc:
            st.error(f"Agent execution failed: {type(exc).__name__}: {exc}")
