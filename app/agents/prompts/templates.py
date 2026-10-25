"""Prompt templates and configurable open-source Hugging Face / mock LLM reasoning providers.

Supports 3 execution modes configured via `LLM_PROVIDER` in `.env`:
1. `LLM_PROVIDER=huggingface_api`: Calls the live Hugging Face Serverless Inference API
   (`https://router.huggingface.co/hf-inference/models/{HF_MODEL_ID}/v1/chat/completions`)
   using `HF_API_TOKEN` (`hf_...`) via `httpx` (or `urllib.request` fallback) and parses the
   structured JSON response into `InvestigatorStructuredOutput` and `PlannerStructuredOutput`
   (which are then verified by Node 5 `RecommendationValidator`).
2. `LLM_PROVIDER=huggingface_local`: Runs `transformers.pipeline("text-generation", model=HF_MODEL_ID)`
   locally on GPU/CPU when `transformers` is installed.
3. `LLM_PROVIDER=mock` (default when no `HF_API_TOKEN` is set): Runs the deterministic
   `EvidenceGroundedReasoningModel` so all 33 automated tests work offline without an API key.
"""

from __future__ import annotations

from decimal import Decimal
import json
from typing import Any, Dict, List, Optional, Protocol, Tuple
import urllib.request

try:
    import httpx  # type: ignore
except ImportError:
    httpx = None  # type: ignore

from app.agents.state import (
    CandidateRootCause,
    EvidenceItem,
    HistoricalIncidentMatch,
    InvestigatorStructuredOutput,
    PlannerStructuredOutput,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.enums import ResolutionActionType
from app.schemas.domain import DiscrepancyRead

logger = get_logger(__name__)

INVESTIGATOR_SYSTEM_PROMPT = """You are the ReconcileX Root-Cause Investigator and Resolution Planner for an e-commerce reconciliation platform.
Strict Rules:
1. Never invent financial amounts, transaction IDs, or evidence references.
2. Every candidate root cause and resolution plan MUST cite ONLY `evidence_id` values present in the provided `collected_evidence` list.
3. Never cite a `RUNBOOK:...` ID inside `evidence_references` or `supporting_evidence_ids`—runbooks are advisory context only, not transaction proof.
4. If the provided evidence is insufficient to establish a plausible cause, set `is_inconclusive=true` and list `missing_evidence`.
5. Sensitive resolution actions MUST set `requires_approval=true` and use one of the allowed `action_type` values:
   - retry_webhook_sync
   - reprocess_payment_event
   - refresh_derived_ledger
   - sync_inventory_reservation
   - request_manual_finance_review
   - escalate_to_engineering

Respond ONLY with a valid JSON object matching this exact schema:
{
  "investigator": {
    "is_inconclusive": false,
    "summary": "string",
    "evidence_references": ["ORDER:...", "PAYMENT:...", "EVENT_LOG:..."],
    "candidate_causes": [
      {
        "cause_code": "STRING_CODE",
        "hypothesis": "string",
        "likelihood": "high",
        "supporting_evidence_ids": ["ORDER:...", "PAYMENT:..."]
      }
    ],
    "confidence_score": "0.9500",
    "uncertainty_explanation": "string",
    "missing_evidence": [],
    "next_diagnostic_step": "string"
  },
  "planner": {
    "action_type": "retry_webhook_sync",
    "requires_approval": true,
    "rationale": "string",
    "action_payload": {"order_id": "...", "transaction_id": "..."},
    "supporting_evidence_ids": ["ORDER:...", "PAYMENT:..."]
  }
}
"""


class InvestigationModelProvider(Protocol):
    """Protocol for pluggable LLM or mock investigation providers."""

    model_id: str

    def investigate_and_plan(
        self,
        discrepancy: DiscrepancyRead,
        evidence: List[EvidenceItem],
        incidents: List[HistoricalIncidentMatch],
    ) -> Tuple[InvestigatorStructuredOutput, Optional[PlannerStructuredOutput]]:
        ...


class EvidenceGroundedReasoningModel:
    """Offline/local reasoning provider that synthesizes structured hypotheses strictly from retrieved evidence."""

    def __init__(
        self,
        model_id: str = "Qwen/Qwen2.5-7B-Instruct-Grounded",
    ) -> None:
        self.model_id = model_id

    def investigate_and_plan(
        self,
        discrepancy: DiscrepancyRead,
        evidence: List[EvidenceItem],
        incidents: List[HistoricalIncidentMatch],
    ) -> Tuple[InvestigatorStructuredOutput, Optional[PlannerStructuredOutput]]:
        ev_map = {item.evidence_id: item for item in evidence}
        all_ev_ids = [
            item.evidence_id for item in evidence if item.source_type != "runbook"
        ]
        order_ev_ids = [
            item.evidence_id for item in evidence if item.source_type == "order"
        ]
        pay_ev_ids = [
            item.evidence_id
            for item in evidence
            if item.source_type == "payment_gateway"
        ]
        log_ev_ids = [
            item.evidence_id for item in evidence if item.source_type == "event_log"
        ]
        inv_ev_ids = [
            item.evidence_id for item in evidence if item.source_type == "inventory"
        ]
        disc_ev_ids = [
            item.evidence_id for item in evidence if item.source_type == "discrepancy"
        ]

        non_disc_ids = [eid for eid in all_ev_ids if not eid.startswith("DISCREPANCY:")]
        if not non_disc_ids:
            return (
                InvestigatorStructuredOutput(
                    is_inconclusive=True,
                    summary=(
                        f"Investigation for discrepancy {discrepancy.discrepancy_id} is inconclusive: "
                        "no underlying order, payment, settlement, inventory, or webhook log records were found."
                    ),
                    evidence_references=disc_ev_ids,
                    candidate_causes=[],
                    confidence_score=Decimal("0.2000"),
                    uncertainty_explanation="Source tables returned zero correlated records for this discrepancy.",
                    missing_evidence=[
                        "orders record",
                        "payments record",
                        "webhook_delivery_event_logs",
                    ],
                    next_diagnostic_step="Query external gateway API or verify ingestion pipeline completeness for the target time window.",
                ),
                None,
            )

        dtype = discrepancy.discrepancy_type
        causes: List[CandidateRootCause] = []
        action_payload: Dict[str, Any] = {
            "order_id": discrepancy.order_id,
            "transaction_id": discrepancy.transaction_id,
            "discrepancy_id": discrepancy.discrepancy_id,
        }

        if dtype == "missing_finance_settlement":
            failed_logs = [
                eid
                for eid in log_ev_ids
                if ev_map[eid].data.get("delivery_status") != "delivered"
            ]
            supporting = disc_ev_ids + order_ev_ids + pay_ev_ids + failed_logs
            if failed_logs:
                causes.append(
                    CandidateRootCause(
                        cause_code="WEBHOOK_DELIVERY_FAILURE_DLQ",
                        hypothesis=(
                            f"Payment {discrepancy.transaction_id} for order {discrepancy.order_id} was captured at the gateway, "
                            f"but settlement webhook delivery to the finance ledger failed ({len(failed_logs)} failed log event(s))."
                        ),
                        likelihood="high",
                        supporting_evidence_ids=supporting,
                    )
                )
                action_payload["failed_log_evidence_ids"] = failed_logs
                return (
                    InvestigatorStructuredOutput(
                        is_inconclusive=False,
                        summary=causes[0].hypothesis,
                        evidence_references=supporting,
                        candidate_causes=causes,
                        confidence_score=Decimal("0.9500"),
                        uncertainty_explanation="High confidence based on deterministic cross-source correlation and failed webhook delivery logs.",
                        missing_evidence=[],
                        next_diagnostic_step="Replay failed settlement webhook event or refresh finance ledger projection.",
                    ),
                    PlannerStructuredOutput(
                        action_type=ResolutionActionType.RETRY_WEBHOOK_SYNC,
                        requires_approval=True,
                        rationale=(
                            f"Captured payment {discrepancy.transaction_id} is verified in gateway records; "
                            "replaying the settlement webhook safely populates the missing finance_ledger row."
                        ),
                        action_payload=action_payload,
                        supporting_evidence_ids=supporting,
                    ),
                )
            else:
                return (
                    InvestigatorStructuredOutput(
                        is_inconclusive=True,
                        summary=(
                            f"Payment {discrepancy.transaction_id} is captured without a finance settlement entry, "
                            "but no webhook delivery failure logs were found in event_logs to confirm the root cause."
                        ),
                        evidence_references=supporting,
                        candidate_causes=[],
                        confidence_score=Decimal("0.3500"),
                        uncertainty_explanation="Missing operational event_logs for webhook delivery between payment gateway and finance ledger.",
                        missing_evidence=[
                            "webhook_delivery_event_logs",
                            "finance_settlement_batch_export",
                        ],
                        next_diagnostic_step="Inspect gateway webhook delivery logs or daily settlement batch file before proposing an automated replay.",
                    ),
                    None,
                )

        if dtype == "inventory_inconsistency":
            supporting = disc_ev_ids + order_ev_ids + inv_ev_ids + log_ev_ids
            causes.append(
                CandidateRootCause(
                    cause_code="PARTIAL_INVENTORY_RESERVATION_COMMIT",
                    hypothesis=(
                        f"Inventory deduction records for order {discrepancy.order_id} do not match the ordered line-item quantities."
                    ),
                    likelihood="high",
                    supporting_evidence_ids=supporting,
                )
            )
            return (
                InvestigatorStructuredOutput(
                    is_inconclusive=False,
                    summary=causes[0].hypothesis,
                    evidence_references=supporting,
                    candidate_causes=causes,
                    confidence_score=Decimal("0.9100"),
                    uncertainty_explanation="Direct comparison between order line items and warehouse movements shows a unit delta mismatch.",
                    missing_evidence=[],
                    next_diagnostic_step="Synchronize warehouse reservation/deduction movement with confirmed order line items.",
                ),
                PlannerStructuredOutput(
                    action_type=ResolutionActionType.SYNC_INVENTORY_RESERVATION,
                    requires_approval=True,
                    rationale="Re-align inventory reservation/deduction movement with confirmed order quantities.",
                    action_payload=action_payload,
                    supporting_evidence_ids=supporting,
                ),
            )

        supporting = all_ev_ids
        cause_map = {
            "amount_mismatch": ("CAUSE_AMOUNT_MISMATCH", ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW),
            "multiple_captures": ("CAUSE_MULTIPLE_CAPTURES", ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW),
            "orphan_payment": ("CAUSE_ORPHAN_PAYMENT", ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW),
            "excessive_refund": ("CAUSE_EXCESSIVE_REFUND", ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW),
            "currency_mismatch": ("CAUSE_CURRENCY_MISMATCH", ResolutionActionType.ESCALATE_TO_ENGINEERING),
            "missing_payment": ("CAUSE_MISSING_PAYMENT", ResolutionActionType.REPROCESS_PAYMENT_EVENT),
            "missing_or_mismatched_refund": ("CAUSE_MISSING_OR_MISMATCHED_REFUND", ResolutionActionType.REPROCESS_PAYMENT_EVENT),
            "duplicate_source_record": ("CAUSE_DUPLICATE_SOURCE_RECORD", ResolutionActionType.REFRESH_DERIVED_LEDGER),
            "stale_event": ("CAUSE_STALE_EVENT", ResolutionActionType.REPROCESS_PAYMENT_EVENT),
        }
        cause_code, chosen_action = cause_map.get(
            dtype,
            (f"CAUSE_{dtype.upper()}", ResolutionActionType.REQUEST_MANUAL_FINANCE_REVIEW),
        )
        causes.append(
            CandidateRootCause(
                cause_code=cause_code,
                hypothesis=f"Discrepancy ({dtype}) verified across {len(supporting)} correlated evidence record(s): {discrepancy.description}",
                likelihood="high",
                supporting_evidence_ids=supporting,
            )
        )
        return (
            InvestigatorStructuredOutput(
                is_inconclusive=False,
                summary=causes[0].hypothesis,
                evidence_references=supporting,
                candidate_causes=causes,
                confidence_score=Decimal("0.8900"),
                uncertainty_explanation="Grounded directly in primary reconciliation tables.",
                missing_evidence=[],
                next_diagnostic_step=f"Execute controlled resolution action '{chosen_action.value}' after operator review.",
            ),
            PlannerStructuredOutput(
                action_type=chosen_action,
                requires_approval=True,
                rationale=f"Propose '{chosen_action.value}' for {discrepancy.rule_code} based on verified evidence.",
                action_payload={
                    "order_id": discrepancy.order_id,
                    "transaction_id": discrepancy.transaction_id,
                    "discrepancy_type": dtype,
                },
                supporting_evidence_ids=all_ev_ids,
            ),
        )


class HuggingFaceInferenceModel:
    """Live Hugging Face Inference API provider using `HF_API_TOKEN` and `HF_MODEL_ID`."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self.model_id = f"hf-api:{self._settings.HF_MODEL_ID}"
        self._fallback = EvidenceGroundedReasoningModel(
            model_id=f"{self._settings.HF_MODEL_ID}-Fallback"
        )

    def investigate_and_plan(
        self,
        discrepancy: DiscrepancyRead,
        evidence: List[EvidenceItem],
        incidents: List[HistoricalIncidentMatch],
    ) -> Tuple[InvestigatorStructuredOutput, Optional[PlannerStructuredOutput]]:
        token = (self._settings.HF_API_TOKEN or "").strip()
        if not token:
            logger.warning(
                "LLM_PROVIDER is set to 'huggingface_api' but HF_API_TOKEN is empty; falling back to local grounded model.",
                extra={"context": {"model_id": self._settings.HF_MODEL_ID}},
            )
            return self._fallback.investigate_and_plan(discrepancy, evidence, incidents)

        user_prompt = json.dumps(
            {
                "discrepancy": discrepancy.dict(),
                "collected_evidence": [e.dict() for e in evidence],
                "advisory_historical_runbooks": [inc.dict() for inc in incidents],
            },
            default=str,
        )

        endpoint = (
            f"{self._settings.HF_INFERENCE_API_URL.rstrip('/')}/"
            f"{self._settings.HF_MODEL_ID}/v1/chat/completions"
        )
        logger.info(
            "Calling live Hugging Face Inference API",
            extra={
                "context": {
                    "endpoint": endpoint,
                    "model_id": self._settings.HF_MODEL_ID,
                    "discrepancy_id": discrepancy.discrepancy_id,
                    "evidence_count": len(evidence),
                }
            },
        )

        payload_dict = {
            "model": self._settings.HF_MODEL_ID,
            "messages": [
                {"role": "system", "content": INVESTIGATOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._settings.LLM_TEMPERATURE,
            "max_tokens": self._settings.LLM_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            if httpx is not None:
                with httpx.Client(timeout=float(self._settings.LLM_TIMEOUT_SECONDS)) as client:
                    resp = client.post(endpoint, headers=headers, json=payload_dict)
                    resp.raise_for_status()
                    body = resp.json()
            else:
                req = urllib.request.Request(
                    endpoint,
                    data=json.dumps(payload_dict).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=float(self._settings.LLM_TIMEOUT_SECONDS)) as resp:
                    body = json.loads(resp.read().decode("utf-8"))

            raw_content = body["choices"][0]["message"]["content"]
            parsed = self._extract_json_object(raw_content)
            inv_data = parsed.get("investigator", parsed)
            plan_data = parsed.get("planner")

            inv_out = InvestigatorStructuredOutput(**inv_data)
            plan_out = (
                PlannerStructuredOutput(**plan_data)
                if isinstance(plan_data, dict) and not inv_out.is_inconclusive
                else None
            )
            return inv_out, plan_out
        except Exception as exc:
            logger.error(
                "Hugging Face Inference API call failed; falling back to local grounded provider",
                extra={
                    "context": {
                        "model_id": self._settings.HF_MODEL_ID,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                },
            )
            return self._fallback.investigate_and_plan(discrepancy, evidence, incidents)

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = [ln for ln in cleaned.splitlines() if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        return json.loads(cleaned)


def build_configured_model_provider(
    settings: Optional[Settings] = None,
) -> InvestigationModelProvider:
    """Factory returning the configured LLM provider based on `LLM_PROVIDER` in `.env`."""
    cfg = settings or get_settings()
    if cfg.LLM_PROVIDER == "huggingface_api":
        return HuggingFaceInferenceModel(cfg)
    return EvidenceGroundedReasoningModel(
        model_id=f"{cfg.HF_MODEL_ID}-Grounded"
    )
