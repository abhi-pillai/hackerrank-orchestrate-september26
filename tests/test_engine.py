"""
Unit tests for the deterministic core (forecast, optimizer, validator),
built from small synthetic FinancialState fixtures rather than the full
dataset, so they run independently of dataset/ and of any LLM.

Run with:
    python3 -m pytest tests/test_engine.py -v
or:
    python3 tests/test_engine.py
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from decimal import Decimal

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from code.financial_state import CashEvent, FinancialState
from code.forecast import run_forecast, is_amount_safe_on_date
from code.models import PlannedPayment, SpendingChange
from code.optimizer import calculate_amount_safe_to_pay, find_earliest_full_payment_date
from code.validator import validate_recommendation
from code.models import Recommendation


def make_state(**overrides) -> FinancialState:
    defaults = dict(
        user_id="user_test",
        request_id="request_test",
        request_date=date(2026, 1, 1),
        requested_amount=Decimal("1000"),
        home_currency="USD",
        current_balance=Decimal("2000"),
        minimum_balance=Decimal("500"),
        desired_completion_date=date(2026, 2, 1),
        priorities=[],
        payment_preferences={"full_payment", "partial_payment", "installments"},
        max_installment_months=6,
        forecast_horizon_end=date(2026, 3, 31),
        cash_events=[],
        payment_options=[],
        evidence_notes=[],
    )
    defaults.update(overrides)
    return FinancialState(**defaults)


class TestForecast(unittest.TestCase):
    def test_exact_minimum_balance_is_safe(self):
        state = make_state(current_balance=Decimal("1500"), minimum_balance=Decimal("500"))
        result = is_amount_safe_on_date(state, Decimal("1000"), state.request_date)
        self.assertTrue(result.safe)
        self.assertEqual(result.min_balance_seen, Decimal("500"))

    def test_below_minimum_balance_is_unsafe(self):
        state = make_state(current_balance=Decimal("1499.99"), minimum_balance=Decimal("500"))
        result = is_amount_safe_on_date(state, Decimal("1000"), state.request_date)
        self.assertFalse(result.safe)

    def test_future_salary_makes_later_date_safe(self):
        events = [
            CashEvent(
                event_id="e_salary", category="salary", direction="credit", amount=Decimal("800"),
                on_date=date(2026, 1, 15), flexibility="fixed", protected=True, reducible=False,
                stoppable=False, minimum_allowed_amount=None,
            )
        ]
        state = make_state(current_balance=Decimal("1000"), minimum_balance=Decimal("500"), cash_events=events)
        # Not safe today (1000-1000=0 < 500) but safe after salary arrives.
        self.assertFalse(is_amount_safe_on_date(state, Decimal("1000"), date(2026, 1, 1)).safe)
        self.assertTrue(is_amount_safe_on_date(state, Decimal("1000"), date(2026, 1, 16)).safe)

    def test_pending_payment_reserved(self):
        events = [
            CashEvent(
                event_id="e_pending", category="debt_repayment", direction="debit", amount=Decimal("400"),
                on_date=date(2026, 1, 10), flexibility="fixed", protected=True, reducible=False,
                stoppable=False, minimum_allowed_amount=None,
            )
        ]
        state = make_state(current_balance=Decimal("1000"), minimum_balance=Decimal("500"), cash_events=events)
        # Paying 500 today plus a 400 pending debit later must still respect
        # the minimum: 1000 - 500 - 400 = 100 < 500 -> unsafe.
        self.assertFalse(is_amount_safe_on_date(state, Decimal("500"), date(2026, 1, 1)).safe)

    def test_recurring_expense_dip(self):
        events = [
            CashEvent(
                event_id=f"e_rent_{i}", category="rent", direction="debit", amount=Decimal("300"),
                on_date=date(2026, 1, d), flexibility="fixed", protected=True, reducible=False,
                stoppable=False, minimum_allowed_amount=None,
            )
            for i, d in enumerate([5, 20])
        ]
        state = make_state(current_balance=Decimal("1200"), minimum_balance=Decimal("500"), cash_events=events)
        result = is_amount_safe_on_date(state, Decimal("300"), date(2026, 1, 1))
        # 1200 - 300(purchase) - 300(rent1) - 300(rent2) = 300 < 500 -> unsafe
        self.assertFalse(result.safe)

    def test_flexible_expense_can_be_reduced(self):
        events = [
            CashEvent(
                event_id="e_dining", category="dining", direction="debit", amount=Decimal("300"),
                on_date=date(2026, 1, 10), flexibility="reducible", protected=False, reducible=True,
                stoppable=False, minimum_allowed_amount=Decimal("50"),
            )
        ]
        state = make_state(current_balance=Decimal("1250"), minimum_balance=Decimal("500"), cash_events=events)
        # Unreduced: 1250 - 500 - 300 = 450 < 500 -> unsafe.
        unreduced = is_amount_safe_on_date(state, Decimal("500"), date(2026, 1, 1))
        # Reduced to the floor of 50: 1250 - 500 - 50 = 700 >= 500 -> safe.
        reduced = is_amount_safe_on_date(
            state, Decimal("500"), date(2026, 1, 1), reduced_amounts={"e_dining": Decimal("50")}
        )
        self.assertFalse(unreduced.safe)
        self.assertTrue(reduced.safe)

    def test_non_flexible_expense_cannot_be_targeted_by_validator(self):
        events = [
            CashEvent(
                event_id="e_rent", category="rent", direction="debit", amount=Decimal("300"),
                on_date=date(2026, 1, 10), flexibility="fixed", protected=True, reducible=False,
                stoppable=False, minimum_allowed_amount=None,
            )
        ]
        state = make_state(cash_events=events)
        rec = Recommendation(
            request_id="request_test", amount_safe_to_pay=Decimal("0"),
            affordability_status="not_affordable", recommended_payment_method="not_recommended",
            payment_plan=[], earliest_date_for_full_payment=None,
            spending_changes_needed=[SpendingChange(kind="stop", event_id="e_rent")],
            decision_explanation="x",
        )
        result = validate_recommendation(state, rec)
        self.assertFalse(result.ok)
        self.assertTrue(any("protected" in e or "not stoppable" in e for e in result.errors))

    def test_currency_conversion_effect(self):
        # Sanity: forecast operates purely in home currency amounts already
        # converted upstream; this test just ensures Decimal math is exact.
        state = make_state(current_balance=Decimal("1000.00"), minimum_balance=Decimal("0"))
        result = is_amount_safe_on_date(state, Decimal("999.99"), state.request_date)
        self.assertTrue(result.safe)
        self.assertEqual(result.min_balance_seen, Decimal("0.01"))

    def test_full_payment_safe_today(self):
        state = make_state(current_balance=Decimal("5000"), minimum_balance=Decimal("100"))
        amt = calculate_amount_safe_to_pay(state)
        self.assertEqual(amt, state.requested_amount)

    def test_not_affordable_when_never_safe(self):
        events = [
            CashEvent(
                event_id="e_big_bill", category="healthcare", direction="debit", amount=Decimal("100000"),
                on_date=date(2026, 1, 5), flexibility="fixed", protected=True, reducible=False,
                stoppable=False, minimum_allowed_amount=None,
            )
        ]
        state = make_state(current_balance=Decimal("2000"), minimum_balance=Decimal("500"), cash_events=events)
        earliest = find_earliest_full_payment_date(state)
        self.assertIsNone(earliest)

    def test_different_minimum_balances_change_outcome(self):
        low_min = make_state(current_balance=Decimal("1500"), minimum_balance=Decimal("100"))
        high_min = make_state(current_balance=Decimal("1500"), minimum_balance=Decimal("600"))
        self.assertTrue(is_amount_safe_on_date(low_min, Decimal("1000"), low_min.request_date).safe)
        self.assertFalse(is_amount_safe_on_date(high_min, Decimal("1000"), high_min.request_date).safe)


if __name__ == "__main__":
    unittest.main()
