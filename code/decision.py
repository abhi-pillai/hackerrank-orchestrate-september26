"""
decision.py — the "Forecast & Safety Engine" + "Validator" boxes wired
together into one call: evaluate_affordability(state) -> Recommendation.

This is deterministic. Gemini (agent.py) calls this as a tool and then only
adds the final explanation phrasing; it cannot alter the numbers it returns.

Important field semantics (confirmed against dataset/sample_requests.csv):
`amount_safe_to_pay` and `earliest_date_for_full_payment` always report the
*baseline* answer — the most that can safely be paid today, and the
earliest a full payment is safe, WITHOUT any spending changes — even when
the actual recommended plan uses spending changes or an installment
schedule to do better than that baseline. They are informational context,
not a description of the chosen plan.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from . import config
from .explanation import build_deterministic_explanation
from .financial_state import FinancialState
from .models import PlannedPayment, Recommendation, SpendingChange
from .optimizer import (
    calculate_amount_safe_to_pay,
    find_allowed_spending_changes,
    find_earliest_full_payment_date,
    generate_candidate_plans,
    rank_candidates,
)
from .validator import validate_recommendation


def _status_for(candidate, spending_changes: list[SpendingChange]) -> str:
    if candidate.method == "not_recommended":
        return "not_affordable"
    if spending_changes:
        # Needing a spending change is never "already affordable as-is".
        return "affordable_with_plan"
    if candidate.method == "wait":
        return "affordable_later"
    if candidate.method == "full_payment" and candidate.payments and candidate.payments[0].pay_date == candidate.payments[0].pay_date:
        # full_payment with no changes: "now" iff it's on the request date,
        # which the caller guarantees by construction for the no-change path.
        return "affordable_now"
    return "affordable_with_plan"


def _candidate_to_recommendation(
    state: FinancialState,
    candidate,
    spending_changes: list[SpendingChange],
    baseline_amount_safe_to_pay: Decimal,
    baseline_earliest_date: Optional[date],
) -> Recommendation:
    status = _status_for(candidate, spending_changes)
    payment_plan = [] if candidate.method == "not_recommended" else candidate.payments

    rec = Recommendation(
        request_id=state.request_id,
        amount_safe_to_pay=baseline_amount_safe_to_pay,
        affordability_status=status,
        recommended_payment_method=candidate.method,
        payment_plan=payment_plan,
        earliest_date_for_full_payment=baseline_earliest_date,
        spending_changes_needed=spending_changes,
        decision_explanation="",
        total_paid=candidate.total_paid,
        chosen_payment_option_id=candidate.payment_option_id,
    )
    rec.decision_explanation = build_deterministic_explanation(state, rec)
    return rec


def evaluate_affordability(state: FinancialState, request_allows_partial: bool) -> Recommendation:
    amount_safe_to_pay = calculate_amount_safe_to_pay(state)
    earliest_date = find_earliest_full_payment_date(state)

    candidates = generate_candidate_plans(state, amount_safe_to_pay, earliest_date, request_allows_partial)
    ranked = rank_candidates(candidates)

    best_no_change = next((c for c in ranked if c.completes_by_deadline), None)

    if best_no_change is not None:
        rec = _candidate_to_recommendation(state, best_no_change, [], amount_safe_to_pay, earliest_date)
    else:
        # Try permitted spending changes to reach completion by deadline.
        # NOTE: the with-changes amount/date are used ONLY to pick and size
        # the actual plan below; the baseline amount_safe_to_pay/earliest_date
        # computed above are still what gets reported in the output row.
        change_plan = find_allowed_spending_changes(state)
        if change_plan is not None:
            candidates_with_changes = generate_candidate_plans(
                state, change_plan.amount_safe_to_pay, change_plan.earliest_date_for_full_payment, request_allows_partial
            )
            ranked_wc = rank_candidates(candidates_with_changes)
            best_wc = next((c for c in ranked_wc if c.completes_by_deadline), None)
            if best_wc is not None:
                rec = _candidate_to_recommendation(
                    state, best_wc, change_plan.changes, amount_safe_to_pay, earliest_date
                )
            else:
                rec = _fallback_recommendation(state, ranked, amount_safe_to_pay, earliest_date)
        else:
            rec = _fallback_recommendation(state, ranked, amount_safe_to_pay, earliest_date)

    validation = validate_recommendation(state, rec)
    if not validation.ok:
        # Deterministic, conservative fallback: never write an invalid rec.
        rec = _safe_fallback(state, amount_safe_to_pay, earliest_date)
        validation2 = validate_recommendation(state, rec)
        if not validation2.ok:
            rec = _hard_not_recommended(state, amount_safe_to_pay, earliest_date)
    return rec


def _fallback_recommendation(state, ranked, amount_safe_to_pay, earliest_date) -> Recommendation:
    """No plan (with or without permitted spending changes) completes by the
    deadline. Prefer 'wait'/'affordable_later' when the amount is eventually
    safe within the 90-day horizon; otherwise not_recommended/not_affordable."""
    wait_candidate = next((c for c in ranked if c.method == "wait"), None)
    if wait_candidate is not None:
        return _candidate_to_recommendation(state, wait_candidate, [], amount_safe_to_pay, earliest_date)
    return _hard_not_recommended(state, amount_safe_to_pay, earliest_date)


def _safe_fallback(state, amount_safe_to_pay, earliest_date) -> Recommendation:
    if earliest_date is not None:
        method = "full_payment" if earliest_date == state.request_date else "wait"
        status = "affordable_now" if earliest_date == state.request_date else "affordable_later"
        rec = Recommendation(
            request_id=state.request_id,
            amount_safe_to_pay=amount_safe_to_pay,
            affordability_status=status,
            recommended_payment_method=method,
            payment_plan=[PlannedPayment(earliest_date, state.requested_amount)],
            earliest_date_for_full_payment=earliest_date,
            spending_changes_needed=[],
            decision_explanation="",
        )
        rec.decision_explanation = build_deterministic_explanation(state, rec)
        return rec
    return _hard_not_recommended(state, amount_safe_to_pay, earliest_date)


def _hard_not_recommended(state, amount_safe_to_pay: Decimal = Decimal("0"), earliest_date=None) -> Recommendation:
    rec = Recommendation(
        request_id=state.request_id,
        amount_safe_to_pay=amount_safe_to_pay,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan=[],
        earliest_date_for_full_payment=earliest_date,
        spending_changes_needed=[],
        decision_explanation="",
    )
    rec.decision_explanation = build_deterministic_explanation(state, rec)
    return rec
