"""
models.py — plain dataclasses mirroring the actual dataset schemas.

Field names intentionally match the CSV column names 1:1 so there is no
translation layer that could silently invent semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: list[str]
    expense_categories_to_protect: set[str]
    expense_categories_user_is_willing_to_reduce: set[str]
    expense_categories_user_is_willing_to_stop: set[str]
    payment_methods_user_will_consider: set[str]
    max_installment_months: Optional[int]


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str  # credit | debit | non_cash
    amount: Optional[Decimal]  # resolved amount (may start as None -> blank)
    currency: str
    event_date: date
    settlement_date: date
    status: str
    linked_event_id: str
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    amount_source: str = "declared"  # declared | image_ocr | image_llm | unresolved


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: str
    related_event_id: str
    sent_at: str
    source_type: str
    message_text: str


@dataclass
class ImageRef:
    image_id: str
    user_id: str
    request_id: str
    related_event_id: str


@dataclass
class ExchangeRate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass
class PlannedPayment:
    pay_date: date
    amount: Decimal


@dataclass
class SpendingChange:
    kind: str  # stop | reduce_to
    event_id: str
    new_amount: Optional[Decimal] = None

    def to_token(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_money(self.new_amount)}"


@dataclass
class Recommendation:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: list[PlannedPayment]
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: list[SpendingChange]
    decision_explanation: str
    # bookkeeping (not written to CSV, used for ranking/logging)
    total_paid: Decimal = Decimal("0")
    chosen_payment_option_id: Optional[str] = None


def fmt_amount(amount: Decimal) -> str:
    """Render a scalar computed amount (amount_safe_to_pay) the way the
    sample outputs do: quantized to 2dp, then trailing zeros (and a bare
    trailing dot) stripped. Matches dataset/sample_requests.csv, e.g. 25256,
    17229139.2, 15952906.67."""
    q = amount.quantize(Decimal("0.01"))
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def fmt_money(amount: Decimal) -> str:
    """Render a currency amount inside payment_plan / spending_changes_needed
    tokens: exactly 2 decimal places when fractional, plain integer when
    whole. Matches dataset/sample_requests.csv payment_plan entries, e.g.
    941.60, 3246.10, 15952906.67, 25256, 13110000."""
    q = amount.quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return str(int(q))
    return format(q, "f")
