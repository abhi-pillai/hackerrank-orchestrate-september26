"""
tools.py — the explicit Python tool set the agent loop can call.

Every tool here is deterministic and auditable. Gemini selects *which* tool
to call and *when*; the tools themselves do all arithmetic, date handling,
and lookups. This is the smallest useful set for this dataset: some tools
suggested in the original brief (get_recurring_expenses, resolve_event_amount,
etc.) are folded into build_financial_state / evaluate_affordability because
the dataset does not require them as separate calls.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Optional

from .data_loader import Dataset
from .decision import evaluate_affordability as _evaluate_affordability
from .financial_state import FinancialStateBuilder, FinancialState
from .models import Request
from .optimizer import calculate_amount_safe_to_pay as _calc_safe, find_earliest_full_payment_date as _find_earliest


class ToolBelt:
    def __init__(self, dataset: Dataset, state_builder: FinancialStateBuilder) -> None:
        self.ds = dataset
        self.state_builder = state_builder
        self._state_cache: dict[str, FinancialState] = {}

    # -- profile / raw evidence lookups --------------------------------------
    def get_user_profile(self, user_id: str) -> dict:
        p = self.ds.profiles[user_id]
        return {
            "user_id": p.user_id,
            "home_currency": p.home_currency,
            "current_available_balance": str(p.current_available_balance),
            "minimum_balance_to_keep": str(p.minimum_balance_to_keep),
            "financial_priorities": p.financial_priorities,
            "expense_categories_to_protect": sorted(p.expense_categories_to_protect),
            "expense_categories_user_is_willing_to_reduce": sorted(p.expense_categories_user_is_willing_to_reduce),
            "expense_categories_user_is_willing_to_stop": sorted(p.expense_categories_user_is_willing_to_stop),
            "payment_methods_user_will_consider": sorted(p.payment_methods_user_will_consider),
            "max_installment_months": p.max_installment_months,
        }

    def get_financial_events(self, user_id: str) -> list[dict]:
        return [
            {
                "event_id": e.event_id,
                "category": e.category,
                "direction": e.direction,
                "amount": str(e.amount) if e.amount is not None else None,
                "currency": e.currency,
                "event_date": e.event_date.isoformat(),
                "settlement_date": e.settlement_date.isoformat(),
                "status": e.status,
                "flexibility": e.flexibility,
            }
            for e in self.ds.events_by_user.get(user_id, [])
        ]

    def get_payment_options(self, request_id: str) -> list[dict]:
        return [
            {
                "payment_option_id": o.payment_option_id,
                "payment_method": o.payment_method,
                "payment_amount": str(o.payment_amount),
                "number_of_payments": o.number_of_payments,
                "first_payment_date": o.first_payment_date.isoformat(),
                "payment_frequency_days": o.payment_frequency_days,
                "financing_fee": str(o.financing_fee),
                "total_payable_amount": str(o.total_payable_amount),
            }
            for o in self.ds.payment_options_by_request.get(request_id, [])
        ]

    def search_messages(self, user_id: str, request_id: Optional[str] = None,
                         related_event_id: Optional[str] = None) -> list[dict]:
        msgs = self.ds.messages_by_user.get(user_id, [])
        out = []
        for m in msgs:
            if request_id and m.request_id != request_id:
                continue
            if related_event_id and m.related_event_id != related_event_id:
                continue
            out.append(
                {
                    "message_id": m.message_id,
                    "sent_at": m.sent_at,
                    "source_type": m.source_type,
                    "message_text": m.message_text,
                    "related_event_id": m.related_event_id,
                }
            )
        return out

    def get_image_evidence(self, request_id: Optional[str] = None,
                            related_event_id: Optional[str] = None) -> list[dict]:
        out = []
        for img in self.ds.images:
            if request_id and img.request_id != request_id:
                continue
            if related_event_id and img.related_event_id != related_event_id:
                continue
            out.append(asdict(img))
        return out

    # -- state / forecast / decision -----------------------------------------
    def build_financial_state(self, request: Request) -> FinancialState:
        if request.request_id in self._state_cache:
            return self._state_cache[request.request_id]
        state = self.state_builder.build(request)
        self._state_cache[request.request_id] = state
        return state

    def calculate_amount_safe_to_pay(self, request: Request) -> Decimal:
        state = self.build_financial_state(request)
        return _calc_safe(state)

    def find_earliest_full_payment_date(self, request: Request) -> Optional[date]:
        state = self.build_financial_state(request)
        return _find_earliest(state)

    def evaluate_affordability(self, request: Request):
        state = self.build_financial_state(request)
        return _evaluate_affordability(state, request.allows_partial_payment)
