"""
data_loader.py — reads the dataset CSVs into typed in-memory structures.

No field is invented. Every column consumed here exists verbatim in the
dataset schemas discovered by inspection (see README for the full list).
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from . import config
from .models import (
    ExchangeRate,
    FinancialEvent,
    ImageRef,
    Message,
    PaymentOption,
    Profile,
    Request,
)


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _parse_bool(s: str) -> bool:
    return s.strip().lower() == "true"


def _parse_decimal(s: str) -> Optional[Decimal]:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_pipe_list(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.split("|") if x.strip()]


def _parse_pipe_set(s: str) -> set[str]:
    return set(_parse_pipe_list(s))


def load_requests(path: Path = config.REQUESTS_CSV) -> list[Request]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                Request(
                    request_id=row["request_id"],
                    user_id=row["user_id"],
                    request_date=_parse_date(row["request_date"]),
                    request_type=row["request_type"],
                    requested_amount=_parse_decimal(row["requested_amount"]) or Decimal("0"),
                    desired_completion_date=_parse_date(row["desired_completion_date"]),
                    allows_partial_payment=_parse_bool(row["allows_partial_payment"]),
                    request_text=row.get("request_text", "") or "",
                )
            )
    return out


def load_profiles(path: Path = config.PROFILES_CSV) -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            max_months_raw = (row.get("max_installment_months") or "").strip()
            out[row["user_id"]] = Profile(
                user_id=row["user_id"],
                home_currency=row["home_currency"],
                current_available_balance=_parse_decimal(row["current_available_balance"]) or Decimal("0"),
                minimum_balance_to_keep=_parse_decimal(row["minimum_balance_to_keep"]) or Decimal("0"),
                financial_priorities=_parse_pipe_list(row.get("financial_priorities", "")),
                expense_categories_to_protect=_parse_pipe_set(row.get("expense_categories_to_protect", "")),
                expense_categories_user_is_willing_to_reduce=_parse_pipe_set(
                    row.get("expense_categories_user_is_willing_to_reduce", "")
                ),
                expense_categories_user_is_willing_to_stop=_parse_pipe_set(
                    row.get("expense_categories_user_is_willing_to_stop", "")
                ),
                payment_methods_user_will_consider=_parse_pipe_set(
                    row.get("payment_methods_user_will_consider", "")
                ),
                max_installment_months=int(max_months_raw) if max_months_raw else None,
            )
    return out


def load_events(path: Path = config.EVENTS_CSV) -> list[FinancialEvent]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            amount = _parse_decimal(row["amount"])
            out.append(
                FinancialEvent(
                    event_id=row["event_id"],
                    user_id=row["user_id"],
                    event_type=row["event_type"],
                    description=row.get("description", "") or "",
                    category=row["category"],
                    direction=row["direction"],
                    amount=amount,
                    currency=row["currency"],
                    event_date=_parse_date(row["event_date"]),
                    settlement_date=_parse_date(row["settlement_date"]) if row.get("settlement_date") else _parse_date(row["event_date"]),
                    status=row["status"],
                    linked_event_id=(row.get("linked_event_id") or "").strip(),
                    flexibility=row["flexibility"],
                    minimum_allowed_amount=_parse_decimal(row.get("minimum_allowed_amount", "")),
                    amount_source="declared" if amount is not None else "unresolved",
                )
            )
    return out


def load_payment_options(path: Path = config.PAYMENT_OPTIONS_CSV) -> list[PaymentOption]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            freq = (row.get("payment_frequency_days") or "").strip()
            out.append(
                PaymentOption(
                    payment_option_id=row["payment_option_id"],
                    request_id=row["request_id"],
                    payment_method=row["payment_method"],
                    payment_amount=_parse_decimal(row["payment_amount"]) or Decimal("0"),
                    number_of_payments=int(row["number_of_payments"]),
                    first_payment_date=_parse_date(row["first_payment_date"]),
                    payment_frequency_days=int(freq) if freq else None,
                    financing_fee=_parse_decimal(row.get("financing_fee", "")) or Decimal("0"),
                    total_payable_amount=_parse_decimal(row.get("total_payable_amount", "")) or Decimal("0"),
                )
            )
    return out


def load_messages(path: Path = config.MESSAGES_CSV) -> list[Message]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                Message(
                    message_id=row["message_id"],
                    user_id=row["user_id"],
                    request_id=(row.get("request_id") or "").strip(),
                    related_event_id=(row.get("related_event_id") or "").strip(),
                    sent_at=row["sent_at"],
                    source_type=row["source_type"],
                    message_text=row.get("message_text", "") or "",
                )
            )
    return out


def load_images(path: Path = config.IMAGES_CSV) -> list[ImageRef]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                ImageRef(
                    image_id=row["image_id"],
                    user_id=row["user_id"],
                    request_id=(row.get("request_id") or "").strip(),
                    related_event_id=(row.get("related_event_id") or "").strip(),
                )
            )
    return out


def load_exchange_rates(path: Path = config.EXCHANGE_RATES_CSV) -> list[ExchangeRate]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(
                ExchangeRate(
                    rate_date=_parse_date(row["rate_date"]),
                    from_currency=row["from_currency"],
                    to_currency=row["to_currency"],
                    rate=_parse_decimal(row["rate"]) or Decimal("1"),
                )
            )
    return out


class Dataset:
    """A single load of all input files, indexed for fast lookup."""

    def __init__(self) -> None:
        self.requests: list[Request] = load_requests()
        self.profiles: dict[str, Profile] = load_profiles()
        self.events: list[FinancialEvent] = load_events()
        self.payment_options: list[PaymentOption] = load_payment_options()
        self.messages: list[Message] = load_messages()
        self.images: list[ImageRef] = load_images()
        self.exchange_rates: list[ExchangeRate] = load_exchange_rates()

        self.events_by_user: dict[str, list[FinancialEvent]] = {}
        for e in self.events:
            self.events_by_user.setdefault(e.user_id, []).append(e)
        for lst in self.events_by_user.values():
            lst.sort(key=lambda e: (e.event_date, e.event_id))

        self.events_by_id: dict[str, FinancialEvent] = {e.event_id: e for e in self.events}

        self.payment_options_by_request: dict[str, list[PaymentOption]] = {}
        for po in self.payment_options:
            self.payment_options_by_request.setdefault(po.request_id, []).append(po)

        self.messages_by_user: dict[str, list[Message]] = {}
        for m in self.messages:
            self.messages_by_user.setdefault(m.user_id, []).append(m)

        self.images_by_related_event: dict[str, ImageRef] = {}
        for img in self.images:
            if img.related_event_id:
                self.images_by_related_event[img.related_event_id] = img

    def requests_count(self) -> int:
        return len(self.requests)
