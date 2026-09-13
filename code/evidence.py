"""
evidence.py — resolves untrusted/incomplete evidence into structured facts.

Two responsibilities, matching the "Gemini interprets, Python computes"
split described in the project brief:

1. ImageEvidenceResolver: a blank `amount` on a financial event must be
   resolved from the linked image (dataset/media/images/<image_id>.png).
   We OCR the receipt/bill/payslip locally (deterministic, cached) and pick
   the amount using a keyword-priority heuristic. If a Gemini API key is
   configured, Gemini vision is used as a secondary check / fallback when
   local OCR is inconclusive — never as the sole source of arithmetic.

2. MessageEvidenceResolver: messages are untrusted free text that may
   confirm, amend, delay, or cancel a financial fact. Embedded instructions
   in message text are never treated as commands to this program — they are
   only mined for financial facts (amount / date / cancellation signal).
   Without network access to an LLM this uses a conservative, auditable
   regex/keyword extraction; when GEMINI_API_KEY is configured and reachable
   the agent prefers Gemini's structured interpretation instead (see
   gemini_client.py / agent.py).

Nothing here fabricates a fact that is not present in the source text/image.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from . import config
from .models import FinancialEvent, ImageRef, Message

CURRENCY_CODES = ("INR", "USD", "EUR", "ZAR", "IDR")

# Keyword priority (lower index = more authoritative / more likely to be the
# single payable total on a bill, receipt, or payslip).
AMOUNT_KEYWORD_PRIORITY = [
    "net pay",
    "total payable",
    "amount payable",
    "total due",
    "amount due",
    "grand total",
    "total amount",
    "total",
]

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _clean_number(tok: str) -> Optional[Decimal]:
    tok = tok.strip().replace(",", "")
    if not tok or tok == ".":
        return None
    try:
        return Decimal(tok)
    except InvalidOperation:
        return None


@dataclass
class ImageAmountResult:
    amount: Optional[Decimal]
    currency_hint: Optional[str]
    source: str  # "ocr_keyword" | "ocr_last_number" | "unresolved"
    matched_line: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["amount"] = str(self.amount) if self.amount is not None else None
        return d

    @staticmethod
    def from_json(d: dict) -> "ImageAmountResult":
        return ImageAmountResult(
            amount=Decimal(d["amount"]) if d.get("amount") is not None else None,
            currency_hint=d.get("currency_hint"),
            source=d.get("source", "unresolved"),
            matched_line=d.get("matched_line", ""),
        )


class ImageEvidenceResolver:
    def __init__(self, cache_path: Path = config.IMAGE_EVIDENCE_CACHE, gemini_client=None) -> None:
        self.cache_path = cache_path
        self.gemini_client = gemini_client
        self._cache: dict[str, dict] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def _ocr_text(self, image_path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return ""
        try:
            return pytesseract.image_to_string(Image.open(image_path))
        except Exception:
            return ""

    def _extract_from_text(self, text: str) -> ImageAmountResult:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        lower_lines = [ln.lower() for ln in lines]

        currency_hint = None
        for code in CURRENCY_CODES:
            if code in text or code.lower() in text.lower():
                currency_hint = code
                break

        best: Optional[tuple[int, Decimal, str]] = None  # (priority, amount, line)
        for idx, keyword in enumerate(AMOUNT_KEYWORD_PRIORITY):
            for li, low in enumerate(lower_lines):
                if keyword in low:
                    nums = NUMBER_RE.findall(lines[li])
                    values = [v for v in (_clean_number(n) for n in nums) if v is not None]
                    if not values:
                        continue
                    candidate = values[-1]  # rightmost number on the line
                    if best is None or idx < best[0]:
                        best = (idx, candidate, lines[li])

        if best is not None:
            return ImageAmountResult(
                amount=best[1], currency_hint=currency_hint, source="ocr_keyword", matched_line=best[2]
            )

        # Fallback: last numeric value with a decimal point anywhere in the doc.
        all_numbers = []
        for ln in lines:
            for n in NUMBER_RE.findall(ln):
                v = _clean_number(n)
                if v is not None:
                    all_numbers.append(v)
        if all_numbers:
            return ImageAmountResult(
                amount=all_numbers[-1], currency_hint=currency_hint, source="ocr_last_number"
            )
        return ImageAmountResult(amount=None, currency_hint=currency_hint, source="unresolved")

    def resolve(self, image_ref: ImageRef) -> ImageAmountResult:
        if image_ref.image_id in self._cache:
            return ImageAmountResult.from_json(self._cache[image_ref.image_id])

        image_path = config.MEDIA_DIR / f"{image_ref.image_id}.png"
        result = ImageAmountResult(amount=None, currency_hint=None, source="unresolved")
        if image_path.exists():
            text = self._ocr_text(image_path)
            if text.strip():
                result = self._extract_from_text(text)

        if result.amount is None and self.gemini_client is not None:
            llm_result = self.gemini_client.extract_amount_from_image(image_path)
            if llm_result is not None:
                result = ImageAmountResult(
                    amount=llm_result.get("amount"),
                    currency_hint=llm_result.get("currency"),
                    source="image_llm",
                )

        self._cache[image_ref.image_id] = result.to_json()
        self._save_cache()
        return result


# ---------------------------------------------------------------------------
# Message evidence
# ---------------------------------------------------------------------------

CANCEL_KEYWORDS = [
    "cancelled", "canceled", "no longer", "ended", "has ended", "not confirmed",
    "no off-season income", "no renewal", "dibatalkan", "tidak lagi", "berakhir",
]
PENDING_KEYWORDS = [
    "still pending", "not yet confirmed", "awaiting", "masih menunggu", "belum dikonfirmasi",
]
AMENDMENT_KEYWORDS = [
    "increases", "increased", "revised to", "reduced to", "now expected", "resumes",
    "replaces", "updated", "changed to", "naik menjadi", "berubah", "diperbarui",
]

CURRENCY_AMOUNT_RE = re.compile(
    r"\b(INR|USD|EUR|ZAR|IDR)\s*([\d][\d.,]*)\b", re.IGNORECASE
)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class MessageSignal:
    message_id: str
    intent: str  # "cancelled" | "pending" | "amendment" | "informational"
    currency_amounts: list[tuple[str, Decimal]]
    percent_changes: list[Decimal]
    dates: list[date]
    raw_text: str


class MessageEvidenceResolver:
    """Deterministic, auditable extraction used when no LLM is configured
    (or as a cross-check against the LLM's structured reading). Only mines
    facts already present in the text; never invents amounts or dates."""

    def parse(self, message: Message) -> MessageSignal:
        text = message.message_text or ""
        low = text.lower()

        amounts: list[tuple[str, Decimal]] = []
        for m in CURRENCY_AMOUNT_RE.finditer(text):
            code = m.group(1).upper()
            val = _clean_number(m.group(2))
            if val is not None:
                amounts.append((code, val))

        percents = []
        for m in PERCENT_RE.finditer(text):
            v = _clean_number(m.group(1))
            if v is not None:
                percents.append(v)

        dates = []
        for m in DATE_RE.finditer(text):
            try:
                dates.append(datetime.strptime(m.group(0), "%Y-%m-%d").date())
            except ValueError:
                pass

        intent = "informational"
        if any(k in low for k in CANCEL_KEYWORDS):
            intent = "cancelled"
        elif any(k in low for k in PENDING_KEYWORDS):
            intent = "pending"
        elif any(k in low for k in AMENDMENT_KEYWORDS) or amounts:
            intent = "amendment"

        return MessageSignal(
            message_id=message.message_id,
            intent=intent,
            currency_amounts=amounts,
            percent_changes=percents,
            dates=dates,
            raw_text=text,
        )
