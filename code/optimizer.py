"""
optimizer.py — deterministic financial computation layer.

Implements, in pure Python/Decimal (no LLM involvement):
  - calculate_amount_safe_to_pay
  - find_earliest_full_payment_date
  - find_allowed_spending_changes
  - generate_candidate_plans
  - rank_candidates (per the problem statement's tie-break order)

Gemini never performs this arithmetic; it only sees the *results* to build
an explanation, and the validator (validator.py) re-checks everything before
anything is written to output.csv.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from . import config
from .financial_state import CashEvent, FinancialState
from .forecast import ForecastResult, is_amount_safe_on_date, run_forecast
from .models import PaymentOption, PlannedPayment, Recommendation, SpendingChange, fmt_amount

CENT = Decimal("0.01")


def calculate_amount_safe_to_pay(
    state: FinancialState,
    excluded_event_ids: Optional[set[str]] = None,
    reduced_amounts: Optional[dict[str, Decimal]] = None,
) -> Decimal:
    """Binary search for the maximum amount payable on request_date that
    keeps the 90-day forecast safe. Feasibility is monotonic: a larger
    same-day payment can only reduce every later balance, never increase it,
    so binary search is valid."""
    lo = Decimal("0")
    hi = state.requested_amount
    if hi <= 0:
        return Decimal("0")

    if is_amount_safe_on_date(state, hi, state.request_date, excluded_event_ids, reduced_amounts).safe:
        return hi
    if not is_amount_safe_on_date(state, lo, state.request_date, excluded_event_ids, reduced_amounts).safe:
        return Decimal("0")

    lo_cents, hi_cents = int(lo * 100), int(hi * 100)
    while hi_cents - lo_cents > 1:
        mid_cents = (lo_cents + hi_cents) // 2
        mid = (Decimal(mid_cents) / 100).quantize(CENT)
        if is_amount_safe_on_date(state, mid, state.request_date, excluded_event_ids, reduced_amounts).safe:
            lo_cents = mid_cents
        else:
            hi_cents = mid_cents
    return (Decimal(lo_cents) / 100).quantize(CENT)


def find_earliest_full_payment_date(
    state: FinancialState,
    excluded_event_ids: Optional[set[str]] = None,
    reduced_amounts: Optional[dict[str, Decimal]] = None,
) -> Optional[date]:
    """Scan every day of the 90-day horizon (feasibility is not monotonic in
    date, since income and expenses both arrive over time) and return the
    first date on which paying the full requested_amount as a single payment
    keeps the forecast safe throughout."""
    d = state.request_date
    while d <= state.forecast_horizon_end:
        result = is_amount_safe_on_date(state, state.requested_amount, d, excluded_event_ids, reduced_amounts)
        if result.safe:
            return d
        d += timedelta(days=1)
    return None


# ---------------------------------------------------------------------------
# Spending changes
# ---------------------------------------------------------------------------

@dataclass
class ChangeCandidate:
    event: CashEvent
    change: SpendingChange
    saved_amount: Decimal


def _build_change_candidates(state: FinancialState) -> list[ChangeCandidate]:
    candidates: list[ChangeCandidate] = []
    seen_events: set[str] = set()
    for ce in state.cash_events:
        if ce.direction != "debit" or ce.protected or ce.event_id in seen_events:
            continue
        if ce.on_date < state.request_date or ce.on_date > state.desired_completion_date:
            continue
        if ce.stoppable:
            candidates.append(
                ChangeCandidate(event=ce, change=SpendingChange(kind="stop", event_id=ce.event_id), saved_amount=ce.amount)
            )
            seen_events.add(ce.event_id)
        elif ce.reducible:
            floor = ce.minimum_allowed_amount if ce.minimum_allowed_amount is not None else Decimal("0")
            floor = min(floor, ce.amount)
            saved = ce.amount - floor
            if saved > 0:
                candidates.append(
                    ChangeCandidate(
                        event=ce,
                        change=SpendingChange(kind="reduce_to", event_id=ce.event_id, new_amount=floor),
                        saved_amount=saved,
                    )
                )
                seen_events.add(ce.event_id)
    # Largest saving first: minimal-disruption search tries the fewest,
    # highest-impact changes before reaching for more.
    candidates.sort(key=lambda c: c.saved_amount, reverse=True)
    return candidates


@dataclass
class SpendingChangePlan:
    changes: list[SpendingChange]
    excluded_event_ids: set[str]
    reduced_amounts: dict[str, Decimal]
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: Optional[date]


def find_allowed_spending_changes(state: FinancialState) -> Optional[SpendingChangePlan]:
    """Search combinations (size 1..3) of flexible spending changes that
    improve affordability enough to complete the full request by
    desired_completion_date. Returns the smallest, least-disruptive
    combination that achieves this, or None if no combination of up to 3
    changes helps (or none is needed)."""
    candidates = _build_change_candidates(state)
    if not candidates:
        return None

    baseline_earliest = find_earliest_full_payment_date(state)
    if baseline_earliest is not None and baseline_earliest <= state.desired_completion_date:
        return None  # no spending change needed

    best: Optional[SpendingChangePlan] = None
    max_k = min(config.MAX_SPENDING_CHANGES, len(candidates))
    for k in range(1, max_k + 1):
        for combo in itertools.combinations(candidates, k):
            excluded = {c.event.event_id for c in combo if c.change.kind == "stop"}
            reduced = {c.event.event_id: c.change.new_amount for c in combo if c.change.kind == "reduce_to"}
            earliest = find_earliest_full_payment_date(state, excluded, reduced)
            if earliest is not None and earliest <= state.desired_completion_date:
                safe_amt = calculate_amount_safe_to_pay(state, excluded, reduced)
                plan = SpendingChangePlan(
                    changes=[c.change for c in combo],
                    excluded_event_ids=excluded,
                    reduced_amounts=reduced,
                    amount_safe_to_pay=safe_amt,
                    earliest_date_for_full_payment=earliest,
                )
                if best is None or (len(plan.changes), -plan.amount_safe_to_pay) < (
                    len(best.changes),
                    -best.amount_safe_to_pay,
                ):
                    best = plan
        if best is not None:
            break  # fewest changes first; don't search larger k once k works
    return best


# ---------------------------------------------------------------------------
# Candidate plan generation
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    method: str  # full_payment | partial_payment | installments | wait | not_recommended
    payments: list[PlannedPayment]
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: Optional[date]
    spending_changes: list[SpendingChange]
    payment_option_id: Optional[str]
    total_paid: Decimal
    completes_by_deadline: bool
    safe: bool


def _installment_option_is_safe(state: FinancialState, option: PaymentOption) -> tuple[bool, list[PlannedPayment]]:
    payments = []
    d = option.first_payment_date
    freq = option.payment_frequency_days or 0
    for i in range(option.number_of_payments):
        payments.append(PlannedPayment(pay_date=d, amount=option.payment_amount))
        d = d + timedelta(days=freq) if freq else d
    result = run_forecast(state, payments)
    return result.safe, payments


def generate_candidate_plans(
    state: FinancialState,
    amount_safe_to_pay: Decimal,
    earliest_date_for_full_payment: Optional[date],
    request_allows_partial: bool,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    prefs = state.payment_preferences

    # full_payment today
    if "full_payment" in prefs:
        full_today_safe = amount_safe_to_pay >= state.requested_amount
        candidates.append(
            Candidate(
                method="full_payment",
                payments=[PlannedPayment(state.request_date, state.requested_amount)],
                amount_safe_to_pay=amount_safe_to_pay,
                earliest_date_for_full_payment=state.request_date if full_today_safe else earliest_date_for_full_payment,
                spending_changes=[],
                payment_option_id=None,
                total_paid=state.requested_amount,
                completes_by_deadline=full_today_safe and state.request_date <= state.desired_completion_date,
                safe=full_today_safe,
            )
        )
        # wait: full payment later, once it becomes safe
        if not full_today_safe and earliest_date_for_full_payment is not None:
            candidates.append(
                Candidate(
                    method="wait",
                    payments=[PlannedPayment(earliest_date_for_full_payment, state.requested_amount)],
                    amount_safe_to_pay=amount_safe_to_pay,
                    earliest_date_for_full_payment=earliest_date_for_full_payment,
                    spending_changes=[],
                    payment_option_id=None,
                    total_paid=state.requested_amount,
                    completes_by_deadline=earliest_date_for_full_payment <= state.desired_completion_date,
                    safe=True,
                )
            )

    # partial_payment
    if (
        "partial_payment" in prefs
        and request_allows_partial
        and 0 < amount_safe_to_pay < state.requested_amount
        and earliest_date_for_full_payment is not None
        and earliest_date_for_full_payment <= state.desired_completion_date
    ):
        remainder = state.requested_amount - amount_safe_to_pay
        payments = [
            PlannedPayment(state.request_date, amount_safe_to_pay),
            PlannedPayment(earliest_date_for_full_payment, remainder),
        ]
        result = run_forecast(state, payments)
        candidates.append(
            Candidate(
                method="partial_payment",
                payments=payments,
                amount_safe_to_pay=amount_safe_to_pay,
                earliest_date_for_full_payment=earliest_date_for_full_payment,
                spending_changes=[],
                payment_option_id=None,
                total_paid=state.requested_amount,
                completes_by_deadline=result.safe and earliest_date_for_full_payment <= state.desired_completion_date,
                safe=result.safe,
            )
        )

    # installments (must exactly match a supplied option)
    if "installments" in prefs:
        for option in state.payment_options:
            if option.payment_method != "installments":
                continue
            if state.max_installment_months is None:
                continue
            span_months = (option.number_of_payments * (option.payment_frequency_days or 30)) / 30.0
            if span_months > state.max_installment_months + 1e-9:
                continue
            last_payment_date = option.first_payment_date + timedelta(
                days=(option.payment_frequency_days or 0) * max(0, option.number_of_payments - 1)
            )
            safe, payments = _installment_option_is_safe(state, option)
            candidates.append(
                Candidate(
                    method="installments",
                    payments=payments,
                    amount_safe_to_pay=amount_safe_to_pay,
                    earliest_date_for_full_payment=earliest_date_for_full_payment,
                    spending_changes=[],
                    payment_option_id=option.payment_option_id,
                    total_paid=option.total_payable_amount,
                    completes_by_deadline=safe and last_payment_date <= state.desired_completion_date,
                    safe=safe,
                )
            )

    return candidates


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Section 'Choosing Between Safe Plans' tie-break order:
    1. completes by desired_completion_date
    2. no spending changes
    3. minimize total amount paid
    4. start earlier
    5. fewer payments
    6. lowest payment_option_id
    """
    def key(c: Candidate):
        first_pay = c.payments[0].pay_date if c.payments else date.max
        opt_id = c.payment_option_id or ""
        return (
            0 if c.completes_by_deadline else 1,
            0 if not c.spending_changes else 1,
            c.total_paid,
            first_pay,
            len(c.payments),
            opt_id,
        )

    safe = [c for c in candidates if c.safe]
    return sorted(safe, key=key)
