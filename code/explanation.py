"""
explanation.py — produces decision_explanation text matching the exact
style used in dataset/sample_requests.csv:

  - currency amounts written with thousands separators, e.g. "IDR 15,952,906.67"
  - dates written long-form without a leading zero, e.g. "15 November 2019"
  - short, specific phrasing per outcome (see the templates below, each
    modeled directly on a sample row)

A deterministic, grounded explanation is always built from the *validated*
recommendation (numbers only ever come from the Python engine). When Gemini
is available it is asked to smooth the phrasing of that already-correct
explanation — it is never allowed to change the numbers, dates, or the
recommended method.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from .financial_state import FinancialState
from .models import Recommendation, SpendingChange


def fmt_money_commas(amount: Decimal) -> str:
    """Currency amount with thousands separators, for prose only (never for
    CSV data fields, which stay plain per fmt_amount / fmt_money)."""
    q = amount.quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return f"{int(q):,}"
    return f"{q:,.2f}"


def fmt_date_long(d: date) -> str:
    """'15 November 2019' — day without a leading zero, full month name."""
    return f"{d.day} {calendar.month_name[d.month]} {d.year}"


def _change_phrase(sc: SpendingChange, state: FinancialState, cur: str) -> str:
    ce = next((c for c in state.cash_events if c.event_id == sc.event_id), None)
    label = (ce.description if ce and ce.description else (ce.category if ce else sc.event_id)).strip()
    label = label[0].lower() + label[1:] if label else sc.event_id
    if sc.kind == "stop":
        return f"Stop the {label}"
    return f"Reduce the {label} to {cur} {fmt_money_commas(sc.new_amount)}"


def build_deterministic_explanation(state: FinancialState, rec: Recommendation) -> str:
    cur = state.home_currency
    method = rec.recommended_payment_method
    min_bal = fmt_money_commas(state.minimum_balance)
    req_amt = fmt_money_commas(state.requested_amount)

    if rec.spending_changes_needed:
        changes_text = "; ".join(_change_phrase(sc, state, cur) for sc in rec.spending_changes_needed)
        pay_date = rec.payment_plan[0].pay_date if rec.payment_plan else state.request_date
        pay_amt = fmt_money_commas(rec.payment_plan[0].amount) if rec.payment_plan else req_amt
        when = "today" if pay_date == state.request_date else f"on {fmt_date_long(pay_date)}"
        return f"{changes_text}, then pay {cur} {pay_amt} {when}. This leaves at least {cur} {min_bal} available."

    if method == "full_payment":
        pay_date = rec.payment_plan[0].pay_date if rec.payment_plan else state.request_date
        if pay_date == state.request_date:
            return f"Pay {cur} {req_amt} today. This leaves at least {cur} {min_bal} available over the next 90 days."
        return (
            f"Pay {cur} {req_amt} in full on {fmt_date_long(pay_date)}. "
            f"Paying earlier would take the balance below the {cur} {min_bal} minimum."
        )

    if method == "wait":
        pay_date = rec.payment_plan[0].pay_date if rec.payment_plan else rec.earliest_date_for_full_payment
        return (
            f"Wait until {fmt_date_long(pay_date)}, then pay {cur} {req_amt} in full. "
            f"Paying sooner would put the {cur} {min_bal} minimum at risk."
        )

    if method == "partial_payment":
        p1, p2 = rec.payment_plan
        return (
            f"Pay {cur} {fmt_money_commas(p1.amount)} now, then the remaining {cur} {fmt_money_commas(p2.amount)} "
            f"on {fmt_date_long(p2.pay_date)}. This leaves at least {cur} {min_bal} available."
        )

    if method == "installments":
        n = len(rec.payment_plan)
        per_amt = fmt_money_commas(rec.payment_plan[0].amount) if rec.payment_plan else "0"
        start = fmt_date_long(rec.payment_plan[0].pay_date) if rec.payment_plan else ""
        return (
            f"Use {n} installments of {cur} {per_amt}, starting {start}. "
            f"This leaves at least {cur} {min_bal} available."
        )

    # not_recommended
    return (
        f"Do not make this payment by {fmt_date_long(state.desired_completion_date)}. "
        f"None of the available options keeps the {cur} {min_bal} minimum protected."
    )


def polish_with_gemini(gemini_client, state: FinancialState, rec: Recommendation, base_text: str) -> str:
    if gemini_client is None:
        return base_text
    try:
        polished = gemini_client.polish_explanation(base_text, rec)
        if polished and isinstance(polished, str) and len(polished) < 600:
            return polished
    except Exception:
        pass
    return base_text
