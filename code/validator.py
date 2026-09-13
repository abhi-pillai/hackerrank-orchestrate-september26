"""
validator.py — the final, independent authority. Gemini proposes; this
module (pure Python) is what actually decides whether a recommendation may
be written to output.csv. Nothing reaches the CSV without passing here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import config
from .financial_state import FinancialState
from .forecast import run_forecast
from .models import Recommendation


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_recommendation(state: FinancialState, rec: Recommendation) -> ValidationResult:
    errors: list[str] = []

    # Amount bounds
    if not (Decimal("0") <= rec.amount_safe_to_pay <= state.requested_amount):
        errors.append("amount_safe_to_pay out of [0, requested_amount] range")

    # Enum validity
    if rec.affordability_status not in config.AFFORDABILITY_STATUSES:
        errors.append(f"invalid affordability_status: {rec.affordability_status}")
    if rec.recommended_payment_method not in config.PAYMENT_METHODS:
        errors.append(f"invalid recommended_payment_method: {rec.recommended_payment_method}")

    # affordable_now <-> full_payment on request_date
    if rec.affordability_status == "affordable_now":
        if rec.earliest_date_for_full_payment != state.request_date:
            errors.append("affordable_now requires earliest_date_for_full_payment == request_date")
        if rec.recommended_payment_method != "full_payment":
            errors.append("affordable_now requires recommended_payment_method == full_payment")

    # spending changes: at most 3, only flexible non-protected events, no
    # stop+reduce on the same event
    if len(rec.spending_changes_needed) > config.MAX_SPENDING_CHANGES:
        errors.append("more than 3 spending changes")
    seen_events = set()
    for sc in rec.spending_changes_needed:
        ce = next((c for c in state.cash_events if c.event_id == sc.event_id), None)
        if ce is None:
            errors.append(f"spending change references unknown event {sc.event_id}")
            continue
        if ce.protected:
            errors.append(f"spending change targets protected event {sc.event_id}")
        if sc.kind == "stop" and not ce.stoppable:
            errors.append(f"event {sc.event_id} is not stoppable")
        if sc.kind == "reduce_to" and not ce.reducible:
            errors.append(f"event {sc.event_id} is not reducible")
        if sc.event_id in seen_events:
            errors.append(f"duplicate/conflicting spending change on {sc.event_id}")
        seen_events.add(sc.event_id)

    # payment_plan structural checks
    if rec.recommended_payment_method == "partial_payment":
        if len(rec.payment_plan) != 2:
            errors.append("partial_payment must have exactly two payments")
        else:
            p1, p2 = rec.payment_plan
            if p1.pay_date != state.request_date:
                errors.append("partial_payment first payment must be on request_date")
            total = p1.amount + p2.amount
            if total != state.requested_amount:
                errors.append("partial_payment total does not equal requested_amount")
            if p1.amount != rec.amount_safe_to_pay:
                errors.append("partial_payment first amount must equal amount_safe_to_pay")

    if rec.recommended_payment_method in ("full_payment",):
        if len(rec.payment_plan) != 1 or rec.payment_plan[0].amount != state.requested_amount:
            errors.append("full_payment must be a single payment of the full requested_amount")

    if rec.recommended_payment_method == "wait":
        if len(rec.payment_plan) != 1 or rec.payment_plan[0].amount != state.requested_amount:
            errors.append("wait must be a single future payment of the full requested_amount")

    if rec.recommended_payment_method == "not_recommended":
        if rec.payment_plan:
            errors.append("not_recommended must have an empty ('none') payment plan")

    # chronological order
    dates = [p.pay_date for p in rec.payment_plan]
    if dates != sorted(dates):
        errors.append("payment_plan is not in chronological order")

    # Safety: simulate the full plan end-to-end.
    if rec.recommended_payment_method not in ("wait", "not_recommended", "full_payment") or rec.payment_plan:
        excluded = {sc.event_id for sc in rec.spending_changes_needed if sc.kind == "stop"}
        reduced = {sc.event_id: sc.new_amount for sc in rec.spending_changes_needed if sc.kind == "reduce_to"}
        result = run_forecast(state, rec.payment_plan, excluded, reduced)
        if not result.safe:
            errors.append(
                f"plan is not safe: projected balance {result.min_balance_seen} on "
                f"{result.min_balance_date} < minimum {state.minimum_balance}"
            )

    # affordable_with_plan implies eventual completion of the full amount
    # (installments are exempt from exact equality: their total legitimately
    # includes a financing fee on top of requested_amount).
    if rec.affordability_status == "affordable_with_plan" and rec.recommended_payment_method != "installments":
        total_planned = sum((p.amount for p in rec.payment_plan), Decimal("0"))
        if total_planned != state.requested_amount:
            errors.append("affordable_with_plan must eventually pay the full requested_amount")
    if rec.affordability_status == "affordable_with_plan" and rec.recommended_payment_method == "installments":
        total_planned = sum((p.amount for p in rec.payment_plan), Decimal("0"))
        if total_planned < state.requested_amount:
            errors.append("installment plan pays less than the requested_amount")

    return ValidationResult(ok=not errors, errors=errors)
