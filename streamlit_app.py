"""ReconcileX — Interactive Streamlit Demo & Operations Console.

Drop this single file (`streamlit_app.py`) into the root of your existing
ReconcileX project directory (next to `app/` and `scripts/`) and run:

    pip install streamlit
    streamlit run streamlit_app.py

Requires zero modifications to any existing project files. Connects directly
to your running FastAPI server (`http://127.0.0.1:8000`) and includes a 1-click
synthetic data seeder and empirical evaluation runner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid

import streamlit as st

# Ensure the project root is importable for 1-click local seeding & benchmark runs
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# HTTP Client Helpers (Standard Library — zero extra dependency issues)
# ---------------------------------------------------------------------------
def api_request(
    method: str,
    base_url: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    actor_id: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    """Send an HTTP request to the FastAPI backend and return (status_code, json_body)."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean_params:
            url = f"{url}?{urllib.parse.urlencode(clean_params)}"

    headers: Dict[str, str] = {
        "Accept": "application/json",
        "X-Correlation-ID": f"ui-{uuid.uuid4().hex[:10]}",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    if actor_id:
        headers["X-Actor-Id"] = actor_id

    data_bytes: Optional[bytes] = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url, data=data_bytes, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw_err = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw_err)
        except Exception:
            return exc.code, {"message": raw_err}
    except Exception as exc:
        return 0, {"error": f"Could not connect to {url}: {exc}"}


def seed_local_database() -> Dict[str, Any]:
    """Run the synthetic dataset seeder directly against the configured database."""
    from app.db.session import get_default_session
    from scripts.seed_synthetic import seed_all_synthetic_data

    session = get_default_session()
    return seed_all_synthetic_data(session)


def run_local_evaluation_suite() -> Dict[str, Any]:
    """Run the Phase 7 empirical evaluation benchmark suite."""
    from app.observability.evaluation import run_evaluation_benchmark

    report = run_evaluation_benchmark()
    return report.to_dict()


def format_confidence_display(inv: Dict[str, Any]) -> str:
    """Format confidence score and derived confidence band safely from InvestigationRead."""
    raw_score = inv.get("confidence_score")
    try:
        score_val = float(raw_score) if raw_score is not None else 0.0
    except (TypeError, ValueError):
        score_val = 0.0
    band = inv.get("confidence_band")
    if not band:
        if score_val >= 0.85:
            band = "HIGH"
        elif score_val >= 0.60:
            band = "MEDIUM"
        else:
            band = "LOW"
    return f"{score_val * 100:.1f}% ({str(band).upper()})"


# ---------------------------------------------------------------------------
# Streamlit Page Setup & Session State
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ReconcileX — Agentic Reconciliation Console",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "active_run_id" not in st.session_state:
    st.session_state["active_run_id"] = ""
if "discrepancies" not in st.session_state:
    st.session_state["discrepancies"] = []
if "selected_discrepancy_id" not in st.session_state:
    st.session_state["selected_discrepancy_id"] = ""
if "latest_investigation" not in st.session_state:
    st.session_state["latest_investigation"] = None
if "latest_resolutions" not in st.session_state:
    st.session_state["latest_resolutions"] = []


# ---------------------------------------------------------------------------
# Sidebar: Connection, RBAC Role Switcher & 1-Click Database Seeding
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🛡️ ReconcileX Console")
    st.caption("Deterministic Engine + 5-Node LangGraph Investigator")

    api_base_url = st.text_input(
        "FastAPI Backend URL",
        value=os.environ.get("RECONCILEX_API_URL", "http://127.0.0.1:8000"),
    )

    # Check backend health
    health_code, health_body = api_request("GET", api_base_url, "/health")

    if health_code == 200 and isinstance(health_body, dict):
        st.success(f"Backend Online ({health_body.get('version', '0.1.0')})")
    else:
        st.error(
            "Backend Offline — start `uvicorn app.main:app --reload --port 8000` in your terminal."
        )

    st.divider()
    st.subheader("🔐 Operator RBAC Context")
    role_choice = st.selectbox(
        "Active Role (Tests RBAC Live)",
        options=[
            "operator (dev-operator-key)",
            "admin (dev-admin-key)",
            "viewer (dev-viewer-key — Read Only)",
            "unauthenticated (No API Key)",
        ],
        index=0,
    )
    role_to_key = {
        "operator (dev-operator-key)": "dev-operator-key",
        "admin (dev-admin-key)": "dev-admin-key",
        "viewer (dev-viewer-key — Read Only)": "dev-viewer-key",
        "unauthenticated (No API Key)": None,
    }
    active_api_key = role_to_key[role_choice]
    active_actor_id = st.text_input("Actor ID", value="mehdi-finance-lead")

    st.divider()
    st.subheader("⚡ Quick Setup Actions")
    if st.button("🌱 Seed Synthetic Dataset", use_container_width=True):
        with st.spinner(
            "Seeding synthetic orders, gateway payments, ledger, and DLQ logs..."
        ):
            try:
                summary = seed_local_database()
                st.success("Synthetic dataset seeded!")
                st.json(summary)
            except Exception as exc:
                st.error(f"Seeding failed: {exc}")

    st.caption(
        "💡 **Tip**: Switch between `operator` and `viewer` above to demo how RBAC blocks unauthorized approvals with `HTTP 403`."
    )


# ---------------------------------------------------------------------------
# Main Header & Workflow Banner
# ---------------------------------------------------------------------------
st.markdown(
    """
    ## 🛡️ ReconcileX — E-Commerce Data Reconciliation & Agentic Triage
    **Pipeline Architecture**: `1. Ingest Sources` ➔ `2. Deterministic Decimal Rules (11 Rules)` ➔ `3. LangGraph 5-Node AI Investigation` ➔ `4. Human Approval Gate` ➔ `5. Simulated Execution & Verification`
    """
)

tab_explore, tab_recon, tab_agent, tab_resolve, tab_eval = st.tabs(
    [
        "📊 1. Source Data Explorer",
        "🔍 2. Run Reconciliation",
        "🤖 3. LangGraph AI Inspector",
        "✅ 4. Human Approval & Fix",
        "📈 5. Audit & Benchmark Suite",
    ]
)


# ===========================================================================
# TAB 1: SOURCE DATA EXPLORER & CUSTOM DISCREPANCY INJECTOR
# ===========================================================================
with tab_explore:
    st.subheader("Source Tables in `reconcilex.db`")
    col_filter1, col_filter2, col_refresh = st.columns([2, 2, 1])
    with col_filter1:
        filter_order_id = st.text_input(
            "Filter by Order ID (e.g., DY1042, DY1043, DY1047)", value=""
        )
    with col_filter2:
        table_view = st.selectbox(
            "Select Source Table",
            options=[
                "Orders (/api/v1/orders)",
                "Gateway Payments (/api/v1/payments?source_system=payment_gateway)",
                "Finance Ledger (/api/v1/payments?source_system=finance_ledger)",
                "Refunds (/api/v1/refunds)",
                "Inventory Records (/api/v1/inventory-records)",
                "Webhook & Event Logs (/api/v1/event-logs)",
            ],
        )
    with col_refresh:
        st.write("")
        st.write("")
        st.button("🔄 Refresh Table", use_container_width=True)

    if table_view.startswith("Orders"):
        if filter_order_id.strip():
            code, rows = api_request(
                "GET", api_base_url, f"/api/v1/orders/{filter_order_id.strip()}"
            )
            rows = [rows] if code == 200 else []
        else:
            code, rows = api_request("GET", api_base_url, "/api/v1/orders")
    elif table_view.startswith("Gateway Payments"):
        code, rows = api_request(
            "GET",
            api_base_url,
            "/api/v1/payments",
            params={
                "order_id": filter_order_id.strip(),
                "source_system": "payment_gateway",
            },
        )
    elif table_view.startswith("Finance Ledger"):
        code, rows = api_request(
            "GET",
            api_base_url,
            "/api/v1/payments",
            params={
                "order_id": filter_order_id.strip(),
                "source_system": "finance_ledger",
            },
        )
    elif table_view.startswith("Refunds"):
        code, rows = api_request(
            "GET",
            api_base_url,
            "/api/v1/refunds",
            params={"order_id": filter_order_id.strip()},
        )
    elif table_view.startswith("Inventory"):
        code, rows = api_request(
            "GET",
            api_base_url,
            "/api/v1/inventory-records",
            params={"order_id": filter_order_id.strip()},
        )
    else:
        code, rows = api_request(
            "GET",
            api_base_url,
            "/api/v1/event-logs",
            params={"order_id": filter_order_id.strip()},
        )

    if code == 200 and isinstance(rows, list):
        st.caption(f"Showing **{len(rows)}** record(s)")
        st.dataframe(rows, use_container_width=True)
    else:
        st.warning(
            f"No records returned (HTTP {code}). Click 'Seed Synthetic Dataset' in the sidebar first."
        )

    with st.expander("➕ Inject a Custom Broken Order / Discrepancy for Live Testing"):
        with st.form("inject_custom_order_form"):
            c1, c2, c3 = st.columns(3)
            new_order_id = c1.text_input("New Order ID", value="DY2050")
            new_expected_amt = c2.text_input(
                "Order Expected Amount (Decimal)", value="95000.0000"
            )
            new_captured_amt = c3.text_input(
                "Gateway Captured Amount (Leave different for mismatch)",
                value="90000.0000",
            )
            inject_submit = st.form_submit_button("Inject Order + Gateway Payment")
            if inject_submit:
                o_code, o_res = api_request(
                    "POST",
                    api_base_url,
                    "/api/v1/orders",
                    payload={
                        "order_id": new_order_id,
                        "source_record_id": f"OMS-{new_order_id}",
                        "expected_amount": new_expected_amt,
                        "currency": "INR",
                        "status": "confirmed",
                        "event_timestamp": "2026-10-25T10:00:00Z",
                        "updated_timestamp": "2026-10-25T10:00:00Z",
                    },
                )
                p_code, p_res = api_request(
                    "POST",
                    api_base_url,
                    "/api/v1/payments",
                    payload={
                        "transaction_id": f"TXN-{new_order_id}",
                        "provider_charge_id": f"CHG-{new_order_id}",
                        "order_id": new_order_id,
                        "source_record_id": f"PG-{new_order_id}",
                        "source_system": "payment_gateway",
                        "amount": new_captured_amt,
                        "captured_amount": new_captured_amt,
                        "currency": "INR",
                        "status": "captured",
                        "event_timestamp": "2026-10-25T10:01:00Z",
                        "updated_timestamp": "2026-10-25T10:01:00Z",
                    },
                )
                if o_code in (200, 201) and p_code in (200, 201):
                    st.success(
                        f"Injected `{new_order_id}` (Expected: {new_expected_amt} INR vs Captured: {new_captured_amt} INR, with no finance settlement)! Go to Tab 2 to reconcile."
                    )
                else:
                    st.error(
                        f"Order HTTP {o_code}: {o_res} | Payment HTTP {p_code}: {p_res}"
                    )


# ===========================================================================
# TAB 2: DETERMINISTIC RECONCILIATION ENGINE
# ===========================================================================
with tab_recon:
    st.subheader("Step 2: Run Deterministic Decimal Reconciliation Engine")
    col_run_btn, col_run_id = st.columns([1, 2])

    with col_run_btn:
        if st.button(
            "🚀 Trigger New Reconciliation Run",
            type="primary",
            use_container_width=True,
        ):
            idem_key = f"ui-run-{uuid.uuid4().hex[:8]}"
            r_code, r_body = api_request(
                "POST",
                api_base_url,
                "/api/v1/reconciliations",
                payload={"idempotency_key": idem_key, "triggered_by": active_actor_id},
            )
            if r_code in (200, 201) and isinstance(r_body, dict):
                run_id = r_body.get("run_id", "")
                st.session_state["active_run_id"] = run_id
                d_code, d_body = api_request(
                    "GET",
                    api_base_url,
                    f"/api/v1/reconciliations/{run_id}/discrepancies",
                )
                st.session_state["discrepancies"] = (
                    d_body if d_code == 200 and isinstance(d_body, list) else []
                )
                st.success(
                    f"Reconciliation Run `{run_id[:8]}...` completed with **{len(st.session_state['discrepancies'])}** discrepancy findings!"
                )
            else:
                st.error(f"Failed to trigger reconciliation (HTTP {r_code}): {r_body}")

    with col_run_id:
        st.text_input(
            "Active Reconciliation Run ID",
            value=st.session_state["active_run_id"],
            disabled=True,
        )

    discs: List[Dict[str, Any]] = st.session_state.get("discrepancies", [])
    if discs:
        crit_count = sum(1 for d in discs if d.get("severity") == "critical")
        high_count = sum(1 for d in discs if d.get("severity") == "high")
        med_count = sum(1 for d in discs if d.get("severity") == "medium")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Discrepancies Found", len(discs))
        m2.metric("🔴 Critical Severity", crit_count)
        m3.metric("🟠 High Severity", high_count)
        m4.metric("🟡 Medium Severity", med_count)

        st.markdown("#### Detected Discrepancies")
        summary_rows = [
            {
                "Discrepancy ID": d.get("discrepancy_id", ""),
                "Order ID": d.get("order_id") or "—",
                "Transaction ID": d.get("transaction_id") or "—",
                "Rule Code": d.get("rule_code", ""),
                "Type": d.get("discrepancy_type", ""),
                "Severity": str(d.get("severity", "")).upper(),
                "Status": d.get("status", ""),
                "Expected": d.get("expected_value"),
                "Actual": d.get("actual_value"),
                "Description": d.get("description", ""),
            }
            for d in discs
        ]
        st.dataframe(summary_rows, use_container_width=True)

        st.info(
            "👉 Select any discrepancy below and click **Run 5-Node LangGraph Investigation** in Tab 3."
        )
        disc_options = {
            f"{d.get('order_id') or 'ORPHAN'} | {d.get('rule_code')} ({str(d.get('severity', '')).upper()}) — {d.get('discrepancy_id', '')[:8]}": d.get(
                "discrepancy_id", ""
            )
            for d in discs
        }
        chosen_label = st.selectbox(
            "Select Discrepancy to Investigate", list(disc_options.keys())
        )
        if chosen_label:
            st.session_state["selected_discrepancy_id"] = disc_options[chosen_label]
    else:
        st.info(
            "Click **Trigger New Reconciliation Run** above to scan all source tables."
        )


# ===========================================================================
# TAB 3: LANGGRAPH 5-NODE AI INVESTIGATION INSPECTOR
# ===========================================================================
with tab_agent:
    st.subheader("Step 3: Constrained 5-Node LangGraph Discrepancy Investigator")

    discs_for_agent: List[Dict[str, Any]] = st.session_state.get("discrepancies", [])
    if discs_for_agent:
        agent_options = {
            f"{d.get('order_id') or 'ORPHAN'} | {d.get('rule_code')} — {d.get('discrepancy_id')}": d.get(
                "discrepancy_id", ""
            )
            for d in discs_for_agent
        }
        selected_disc_id = st.selectbox(
            "Target Discrepancy",
            options=list(agent_options.values()),
            format_func=lambda x: next(
                (k for k, v in agent_options.items() if v == x), x
            ),
            index=0,
        )
    else:
        selected_disc_id = st.text_input(
            "Enter Discrepancy ID to Investigate",
            value=st.session_state.get("selected_discrepancy_id", ""),
        )

    if st.button("🤖 Run 5-Node LangGraph AI Investigation", type="primary"):
        if not selected_disc_id:
            st.warning(
                "Run a reconciliation in Tab 2 first to select a Discrepancy ID."
            )
        else:
            with st.spinner("Executing LangGraph Nodes 1 ➔ 2 ➔ 3 & 4 ➔ 5..."):
                i_code, i_body = api_request(
                    "POST",
                    api_base_url,
                    f"/api/v1/discrepancies/{selected_disc_id}/investigations",
                )
                if i_code in (200, 201) and isinstance(i_body, dict):
                    st.session_state["latest_investigation"] = i_body
                    st.session_state["selected_discrepancy_id"] = selected_disc_id
                    res_code, res_body = api_request(
                        "GET",
                        api_base_url,
                        f"/api/v1/discrepancies/{selected_disc_id}/resolutions",
                    )
                    st.session_state["latest_resolutions"] = (
                        res_body
                        if res_code == 200 and isinstance(res_body, list)
                        else []
                    )
                    st.success(
                        f"Investigation `{str(i_body.get('investigation_id', ''))[:8]}...` finished in **{i_body.get('latency_ms', 0)} ms** using `{i_body.get('llm_model_id', 'Qwen/Qwen2.5-7B-Instruct-Grounded')}`!"
                    )
                else:
                    st.error(f"Investigation failed (HTTP {i_code}): {i_body}")

    inv = st.session_state.get("latest_investigation")
    if isinstance(inv, dict) and inv:
        st.divider()
        c_meta1, c_meta2, c_meta3, c_meta4 = st.columns(4)
        c_meta1.metric(
            "Investigation Status", str(inv.get("status", "unknown")).upper()
        )
        c_meta2.metric(
            "Active Model Provider",
            str(inv.get("llm_model_id") or "Qwen/Qwen2.5-7B-Instruct-Grounded"),
        )
        c_meta3.metric(
            "Confidence Score",
            format_confidence_display(inv),
        )
        c_meta4.metric(
            "Node 5 Safety Validator",
            "PASSED ✅" if inv.get("validation_passed") else "REJECTED ❌",
        )

        # Visual 5-Node Breakdown
        st.markdown("### 🔗 Step-by-Step LangGraph Node Execution Trace")

        with st.expander(
            "📥 Node [1/5] EvidenceCollector — Read-Only Cross-Table Evidence",
            expanded=True,
        ):
            st.write(
                "Deterministic read-only queries executed across `orders`, `payments`, `finance_ledger`, `refunds`, `inventory_records`, and `event_logs`:"
            )
            st.code(", ".join(inv.get("evidence_references", [])), language="text")

        with st.expander(
            "📚 Node [2/5] HistoricalIncidentRetriever — Advisory Runbook Match",
            expanded=True,
        ):
            st.caption(
                "Runbooks are tagged `is_advisory_context_only=True` — semantic similarity is never accepted as proof of a transaction match."
            )
            st.info(
                f"**Next Diagnostic Step**: {inv.get('next_diagnostic_step') or 'Review collected evidence'}"
            )

        with st.expander(
            "🧠 Nodes [3/5 & 4/5] RootCauseInvestigator & ResolutionPlanner — Structured LLM Output",
            expanded=True,
        ):
            st.markdown(f"**Executive Summary**: {inv.get('summary') or '—'}")
            st.markdown(
                f"**Uncertainty Note**: {inv.get('uncertainty_explanation') or '—'}"
            )
            if inv.get("candidate_causes"):
                st.markdown(
                    "**Candidate Root Causes & Supporting Evidence Citations:**"
                )
                st.json(inv["candidate_causes"])
            if inv.get("missing_evidence"):
                st.warning(
                    f"**Missing Evidence Identified**: {inv['missing_evidence']}"
                )

        with st.expander(
            "🛡️ Node [5/5] RecommendationValidator — Deterministic Anti-Hallucination Gate",
            expanded=True,
        ):
            if inv.get("validation_passed"):
                st.success(
                    "All cited `evidence_id` references exist in collected DB records, zero `RUNBOOK:` IDs were cited as transaction proof, `requires_approval=True` is enforced, and no prohibited `raw_sql` keys were present."
                )
            else:
                st.error(
                    "Node 5 blocked the LLM output due to safety validation errors:"
                )
                st.json(inv.get("validation_errors", []))


# ===========================================================================
# TAB 4: HUMAN-IN-THE-LOOP APPROVAL, EXECUTION & AUTO-VERIFICATION
# ===========================================================================
with tab_resolve:
    st.subheader(
        "Step 4: Operator Approval Gate, Simulated Fix & Post-Resolution Verification"
    )

    target_disc_for_res = st.session_state.get("selected_discrepancy_id", "")
    if st.button("🔄 Load Proposed Resolutions for Selected Discrepancy"):
        if target_disc_for_res:
            r_code, r_list = api_request(
                "GET",
                api_base_url,
                f"/api/v1/discrepancies/{target_disc_for_res}/resolutions",
            )
            if r_code == 200 and isinstance(r_list, list):
                st.session_state["latest_resolutions"] = r_list

    resolutions: List[Dict[str, Any]] = st.session_state.get("latest_resolutions", [])
    if not resolutions:
        st.info(
            "Run an AI Investigation in Tab 3 first to generate a proposed `ResolutionAction`."
        )
    else:
        for res in resolutions:
            res_id = str(res.get("resolution_id", ""))
            with st.container(border=True):
                h1, h2, h3 = st.columns([2, 1, 1])
                h1.markdown(f"#### Action: `{res.get('action_type', '')}`")
                h2.markdown(f"**Status**: `{str(res.get('status', '')).upper()}`")
                h3.markdown(f"**Proposed By**: `{res.get('proposed_by', '')}`")

                st.write(f"**Rationale**: {res.get('rationale', '')}")
                st.caption(
                    f"**Resolution ID**: `{res_id}` | **Evidence**: `{', '.join(res.get('evidence_references', []))}`"
                )

                operator_comment = st.text_input(
                    "Operator Approval / Rejection Comment",
                    value="Verified evidence citations and DLQ logs; approving controlled action.",
                    key=f"comment_{res_id}",
                )

                b_approve, b_reject, b_exec = st.columns(3)

                if b_approve.button(
                    "✅ Approve Action", key=f"app_{res_id}", use_container_width=True
                ):
                    code, body = api_request(
                        "POST",
                        api_base_url,
                        f"/api/v1/resolutions/{res_id}/approve",
                        payload={"comment": operator_comment},
                        api_key=active_api_key,
                        actor_id=active_actor_id,
                    )
                    if code == 200:
                        st.success(f"Approved `{res_id[:8]}...` as `{active_actor_id}`!")
                        st.json(body)
                    else:
                        st.error(f"Approval blocked (HTTP {code}): {body}")

                if b_reject.button(
                    "🚫 Reject Action", key=f"rej_{res_id}", use_container_width=True
                ):
                    code, body = api_request(
                        "POST",
                        api_base_url,
                        f"/api/v1/resolutions/{res_id}/reject",
                        payload={"comment": operator_comment},
                        api_key=active_api_key,
                        actor_id=active_actor_id,
                    )
                    if code == 200:
                        st.warning(f"Rejected `{res_id[:8]}...`")
                        st.json(body)
                    else:
                        st.error(f"Rejection failed (HTTP {code}): {body}")

                if b_exec.button(
                    "⚡ Execute & Auto-Verify",
                    key=f"exe_{res_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    code, body = api_request(
                        "POST",
                        api_base_url,
                        f"/api/v1/resolutions/{res_id}/execute",
                        payload={"simulate_noop": False},
                        api_key=active_api_key,
                        actor_id=active_actor_id,
                    )
                    if code == 200 and isinstance(body, dict):
                        final_status = body.get("status")
                        if final_status == "verified_resolved":
                            st.balloons()
                            st.success(
                                f"🎉 Executed & Verified Resolved! Discrepancy is now fixed in `reconcilex.db` (Verification Run: `{body.get('verification_run_id')}`)."
                            )
                        else:
                            st.warning(
                                f"Action executed (`status={final_status}`). Note: Manual review tickets (`request_manual_finance_review` / `escalate_to_engineering`) create a ticket reference and keep the financial discrepancy open until a human adjusts the ledger."
                            )
                        st.json(body)
                    else:
                        st.error(f"Execution blocked (HTTP {code}): {body}")

                with st.expander(
                    "📜 View Immutable Audit Trail & Approvals for this Resolution"
                ):
                    _, app_list = api_request(
                        "GET", api_base_url, f"/api/v1/resolutions/{res_id}/approvals"
                    )
                    _, aud_list = api_request(
                        "GET", api_base_url, f"/api/v1/resolutions/{res_id}/audit-trail"
                    )
                    st.markdown("**Approvals Table (`approvals`)**")
                    st.json(app_list)
                    st.markdown("**Audit Trail (`audit_events`)**")
                    st.json(aud_list)


# ===========================================================================
# TAB 5: EMPIRICAL EVALUATION BENCHMARK & PROMETHEUS METRICS
# ===========================================================================
with tab_eval:
    st.subheader("Step 5: Empirical Benchmark Suite & Prometheus Observability")
    col_bench, col_prom = st.columns(2)

    with col_bench:
        st.markdown("#### 🧪 Run Ground-Truth & Adversarial Benchmark")
        st.caption(
            "Evaluates all 9 synthetic ground-truth discrepancies plus 3 adversarial hallucination/SQL-injection attempts."
        )
        if st.button("▶️ Run Evaluation Benchmark Suite", type="primary"):
            with st.spinner("Running deterministic + agentic evaluation suite..."):
                try:
                    report = run_local_evaluation_suite()
                    k1, k2, k3 = st.columns(3)
                    k1.metric(
                        "Detection F1 Score",
                        f"{float(report.get('detection_f1', 0.0)):.4f}",
                    )
                    k2.metric(
                        "Citation Correctness",
                        f"{float(report.get('evidence_citation_correctness_rate', 0.0)) * 100:.1f}%",
                    )
                    k3.metric(
                        "Adversarial Rejection",
                        f"{float(report.get('adversarial_rejection_rate', 0.0)) * 100:.1f}%",
                    )
                    st.json(report)
                except Exception as exc:
                    st.error(f"Benchmark execution failed: {exc}")

    with col_prom:
        st.markdown("#### 📈 Live Prometheus Metrics (`/metrics`)")
        if st.button("🔄 Fetch `/metrics` Snapshot"):
            url = f"{api_base_url.rstrip('/')}/metrics"
            try:
                with urllib.request.urlopen(url, timeout=10.0) as resp:
                    metrics_text = resp.read().decode("utf-8")
                st.code(metrics_text, language="text")
            except Exception as exc:
                st.error(f"Failed to fetch /metrics: {exc}")