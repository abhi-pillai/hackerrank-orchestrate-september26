#!/usr/bin/env python3
"""
main.py — runtime entry point for the "Buy or Wait?" agent.

    python3 code/main.py

Loads dataset/, builds the Gemini agent + deterministic financial engine,
processes every row in dataset/requests.csv, and writes (at the repository
root, generated fresh on every run — nothing here is precomputed/hardcoded):

    output.csv
    agents.md
    evaluation/usage_report.md
"""
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone

# Allow `python3 code/main.py` to work by putting the repo root on sys.path
# so the sibling modules can be imported as the `code` package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from code import config  # noqa: E402
from code.agent import run_agent_for_request  # noqa: E402
from code.data_loader import Dataset  # noqa: E402
from code.evidence import ImageEvidenceResolver, MessageEvidenceResolver  # noqa: E402
from code.financial_state import FinancialStateBuilder  # noqa: E402
from code.fx import ExchangeRateTable  # noqa: E402
from code.gemini_client import GeminiClient  # noqa: E402
from code.logger import AgentLogger  # noqa: E402
from code.models import fmt_amount, fmt_money  # noqa: E402
from code.tools import ToolBelt  # noqa: E402

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def _format_payment_plan(rec) -> str:
    if not rec.payment_plan:
        return "none"
    return "|".join(f"{p.pay_date.isoformat()}:{fmt_money(p.amount)}" for p in rec.payment_plan)


def _format_spending_changes(rec) -> str:
    if not rec.spending_changes_needed:
        return "none"
    return "|".join(sc.to_token() for sc in rec.spending_changes_needed)


def main() -> int:
    run_start = time.time()
    print("Loading dataset...")
    dataset = Dataset()
    fx = ExchangeRateTable(dataset.exchange_rates)

    print("Initializing Gemini client...")
    gemini = GeminiClient()
    if gemini.available:
        print(f"  Gemini available. Model: {config.GEMINI_MODEL}")
    else:
        print("  Gemini not available (no GEMINI_API_KEY, SDK missing, or client init failed).")
        print("  Falling back to the deterministic financial engine for every request, as required.")

    image_resolver = ImageEvidenceResolver(gemini_client=gemini if gemini.available else None)
    message_resolver = MessageEvidenceResolver()
    state_builder = FinancialStateBuilder(dataset, fx, image_resolver, message_resolver)
    tools = ToolBelt(dataset, state_builder)
    logger = AgentLogger()

    rows_out = []
    llm_used_count = 0
    failures = 0

    total = dataset.requests_count()
    print(f"Processing {total} requests...")
    for i, request in enumerate(dataset.requests, start=1):
        try:
            result = run_agent_for_request(request, tools, gemini, logger)
            rec = result.recommendation
            if result.used_llm:
                llm_used_count += 1
            rows_out.append(
                {
                    "request_id": rec.request_id,
                    "amount_safe_to_pay": fmt_amount(rec.amount_safe_to_pay),
                    "affordability_status": rec.affordability_status,
                    "recommended_payment_method": rec.recommended_payment_method,
                    "payment_plan": _format_payment_plan(rec),
                    "earliest_date_for_full_payment": rec.earliest_date_for_full_payment.isoformat()
                    if rec.earliest_date_for_full_payment
                    else "",
                    "spending_changes_needed": _format_spending_changes(rec),
                    "decision_explanation": rec.decision_explanation,
                }
            )
        except Exception as exc:
            failures += 1
            logger.failure(request.request_id, f"{type(exc).__name__}: {exc}")
            rows_out.append(
                {
                    "request_id": request.request_id,
                    "amount_safe_to_pay": "0",
                    "affordability_status": "not_affordable",
                    "recommended_payment_method": "not_recommended",
                    "payment_plan": "none",
                    "earliest_date_for_full_payment": "",
                    "spending_changes_needed": "none",
                    "decision_explanation": "Processing failed; see agents.md for the logged error.",
                }
            )
        if i % 25 == 0 or i == total:
            print(f"  ...{i}/{total}")

    config.OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(config.OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows_out:
            writer.writerow(row)
    print(f"Wrote {len(rows_out)} rows to {config.OUTPUT_CSV}")

    logger.flush()
    print(f"Wrote agent audit trail to {config.AGENTS_LOG_MD}")

    duration = time.time() - run_start
    _write_usage_report(gemini, total, llm_used_count, failures, duration)
    print(f"Wrote {config.USAGE_REPORT_MD}")

    return 0


def _write_usage_report(gemini: GeminiClient, total_requests: int, llm_used_count: int, failures: int, duration_s: float) -> None:
    u = gemini.usage
    avg_tokens = (u.total_tokens / u.calls) if u.calls else 0
    lines = []
    lines.append("# Token Usage and Cost Report\n")
    lines.append(f"\nGenerated: {datetime.now(timezone.utc).isoformat()}\n")
    lines.append("\nThis report reflects the run that produced `output.csv` in this repository.\n")
    lines.append("\n## Summary\n")
    lines.append(f"\n- Requests processed: {total_requests}\n")
    lines.append(f"- Requests where the Gemini agent loop produced the accepted recommendation: {llm_used_count}\n")
    lines.append(
        f"- Requests handled by the deterministic fallback (no LLM / validation rejected the LLM path): "
        f"{total_requests - llm_used_count}\n"
    )
    lines.append(f"- Requests that raised an unhandled error: {failures}\n")
    lines.append(f"- Total run duration: {duration_s:.2f}s\n")
    lines.append("\n## Model\n")
    lines.append(f"\n- Provider: {u.provider}\n")
    lines.append(f"- Model: {u.model}\n")
    lines.append(f"- Gemini SDK available and reachable this run: {gemini.available}\n")
    lines.append("\n## Calls\n")
    lines.append(f"\n- Model calls: {u.calls}\n")
    lines.append(f"- Vision calls: {u.vision_calls}\n")
    lines.append(f"- Retries: {u.retries}\n")
    lines.append(f"- Failures (after retries): {u.failures}\n")
    lines.append("\n## Tokens\n")
    lines.append(f"\n- Input tokens: {u.input_tokens}\n")
    lines.append(f"- Output tokens: {u.output_tokens}\n")
    lines.append(f"- Total tokens: {u.total_tokens}\n")
    lines.append(f"- Average tokens per model call: {avg_tokens:.2f}\n")
    lines.append(
        "\nIf the Gemini SDK was not installed/configured, or the API could not be reached from this "
        "environment, `calls` is 0 and every request was solved by the deterministic financial engine "
        "described in the README. No token or cost figures are estimated or fabricated in that case.\n"
    )
    lines.append("\n## Estimated cost\n")
    if u.calls:
        in_price = os.getenv("GEMINI_INPUT_PRICE_PER_1K")
        out_price = os.getenv("GEMINI_OUTPUT_PRICE_PER_1K")
        if in_price and out_price:
            cost = (u.input_tokens / 1000) * float(in_price) + (u.output_tokens / 1000) * float(out_price)
            lines.append(
                f"\n- Estimated total cost: {cost:.4f} (based on GEMINI_INPUT_PRICE_PER_1K/GEMINI_OUTPUT_PRICE_PER_1K)\n"
            )
            if total_requests:
                lines.append(f"- Estimated cost per request: {cost / total_requests:.6f}\n")
        else:
            lines.append(
                "\n- Not computed: set GEMINI_INPUT_PRICE_PER_1K and GEMINI_OUTPUT_PRICE_PER_1K "
                "(USD per 1K tokens) to have this report compute an estimated cost from the real token counts above.\n"
            )
    else:
        lines.append("\n- $0.00 — no model calls were made this run.\n")

    config.USAGE_REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    config.USAGE_REPORT_MD.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
