"""
forecast.py — deterministic cash-flow simulation and the core safety rule:

    projected_balance >= minimum_balance_to_keep

must hold on every relevant date across the 90-day horizon, not just on the
purchase date. This module contains no LLM calls; it is pure arithmetic on
Decimal values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from .financial_state import CashEvent, FinancialState
from .models import PlannedPayment


@dataclass
class ForecastPoint:
    on_date: date
    balance: Decimal
    delta: Decimal
    reason: str


@dataclass
class ForecastResult:
    safe: bool
    min_balance_seen: Decimal
    min_balance_date: Optional[date]
    timeline: list[ForecastPoint]


def run_forecast(
    state: FinancialState,
    extra_payments: list[PlannedPayment],
    excluded_event_ids: Optional[set[str]] = None,
    reduced_amounts: Optional[dict[str, Decimal]] = None,
) -> ForecastResult:
    """Simulate the balance from state.request_date through the 90-day
    horizon, applying:
      - all committed cash_events (income/expenses) from the financial state
      - the caller's proposed extra_payments (the candidate purchase plan)
      - optional spending changes: excluded_event_ids (stopped) and
        reduced_amounts (event_id -> new amount)

    Returns whether the minimum balance is respected at every point and the
    tightest margin observed, so candidate plans can be ranked by safety
    margin as well as pass/fail.
    """
    excluded_event_ids = excluded_event_ids or set()
    reduced_amounts = reduced_amounts or {}

    lines: list[tuple[date, Decimal, str]] = []
    for ce in state.cash_events:
        if ce.event_id in excluded_event_ids:
            continue
        amount = ce.amount
        if ce.event_id in reduced_amounts:
            amount = reduced_amounts[ce.event_id]
        signed = amount if ce.direction == "credit" else -amount
        lines.append((ce.on_date, signed, f"{ce.direction}:{ce.category}:{ce.event_id}"))

    for p in extra_payments:
        lines.append((p.pay_date, -p.amount, "proposed_payment"))

    lines.sort(key=lambda t: t[0])

    balance = state.current_balance
    min_balance = balance
    min_date = state.request_date
    timeline = [ForecastPoint(state.request_date, balance, Decimal("0"), "start")]

    for on_date, delta, reason in lines:
        balance += delta
        timeline.append(ForecastPoint(on_date, balance, delta, reason))
        if balance < min_balance:
            min_balance = balance
            min_date = on_date

    safe = min_balance >= state.minimum_balance
    return ForecastResult(safe=safe, min_balance_seen=min_balance, min_balance_date=min_date, timeline=timeline)


def is_amount_safe_on_date(
    state: FinancialState,
    amount: Decimal,
    on_date: date,
    excluded_event_ids: Optional[set[str]] = None,
    reduced_amounts: Optional[dict[str, Decimal]] = None,
) -> ForecastResult:
    if amount <= 0:
        return run_forecast(state, [], excluded_event_ids, reduced_amounts)
    payment = PlannedPayment(pay_date=on_date, amount=amount)
    return run_forecast(state, [payment], excluded_event_ids, reduced_amounts)
