#!/usr/bin/env python3
"""
evaluate_samples.py — compares the engine's own logic against the 25 solved
examples in dataset/sample_requests.csv.

This does NOT hardcode the sample answers into the production code; it runs
the same deterministic pipeline (code/decision.py) used by code/main.py
against each sample's *input* columns and reports exact/field-level matches
against the *provided* solved output columns, purely as a validation report.

Usage:
    python3 evaluation/evaluate_samples.py
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from decimal import Decimal

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from code import config
from code.data_loader import Dataset
from code.evidence import ImageEvidenceResolver, MessageEvidenceResolver
from code.financial_state import FinancialStateBuilder
from code.fx import ExchangeRateTable
from code.models import Request, fmt_amount, fmt_money
from code.tools import ToolBelt

FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def _load_samples():
    with open(config.SAMPLE_REQUESTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _format_plan(rec) -> str:
    if not rec.payment_plan:
        return "none"
    return "|".join(f"{p.pay_date.isoformat()}:{fmt_money(p.amount)}" for p in rec.payment_plan)


def _format_changes(rec) -> str:
    if not rec.spending_changes_needed:
        return "none"
    return "|".join(sc.to_token() for sc in rec.spending_changes_needed)


def main() -> int:
    dataset = Dataset()
    fx = ExchangeRateTable(dataset.exchange_rates)
    image_resolver = ImageEvidenceResolver()
    message_resolver = MessageEvidenceResolver()
    state_builder = FinancialStateBuilder(dataset, fx, image_resolver, message_resolver)
    tools = ToolBelt(dataset, state_builder)

    samples = _load_samples()
    exact_matches = 0
    field_hits = {f: 0 for f in FIELDS}
    mismatches = []

    for row in samples:
        request = Request(
            request_id=row["request_id"],
            user_id=row["user_id"],
            request_date=datetime.strptime(row["request_date"], "%Y-%m-%d").date(),
            request_type=row["request_type"],
            requested_amount=Decimal(row["requested_amount"]),
            desired_completion_date=datetime.strptime(row["desired_completion_date"], "%Y-%m-%d").date(),
            allows_partial_payment=row["allows_partial_payment"].strip().lower() == "true",
            request_text=row.get("request_text", ""),
        )
        try:
            rec = tools.evaluate_affordability(request)
            predicted = {
                "amount_safe_to_pay": fmt_amount(rec.amount_safe_to_pay),
                "affordability_status": rec.affordability_status,
                "recommended_payment_method": rec.recommended_payment_method,
                "payment_plan": _format_plan(rec),
                "earliest_date_for_full_payment": rec.earliest_date_for_full_payment.isoformat()
                if rec.earliest_date_for_full_payment
                else "",
                "spending_changes_needed": _format_changes(rec),
                "decision_explanation": rec.decision_explanation,
            }
        except Exception as exc:
            predicted = {f: f"<ERROR: {exc}>" for f in FIELDS}

        row_mismatches = []
        for f in FIELDS:
            expected = row[f]
            got = predicted[f]
            if f == "decision_explanation":
                continue  # free text; not scored for exact match here
            if expected == got:
                field_hits[f] += 1
            else:
                row_mismatches.append((f, expected, got))

        if not row_mismatches:
            exact_matches += 1
        else:
            mismatches.append((row["request_id"], row_mismatches))

    total = len(samples)
    print(f"Samples evaluated: {total}")
    print(f"Exact matches (excluding decision_explanation): {exact_matches}/{total}")
    print("\nField-level match rates:")
    for f in FIELDS:
        if f == "decision_explanation":
            continue
        print(f"  {f}: {field_hits[f]}/{total}")

    if mismatches:
        print("\nMismatches and likely causes:")
        for request_id, diffs in mismatches:
            print(f"\n  {request_id}:")
            for f, expected, got in diffs:
                print(f"    {f}: expected={expected!r} got={got!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
