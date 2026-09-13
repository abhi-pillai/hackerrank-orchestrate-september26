"""
fx.py — currency conversion strictly from dataset/exchange_rates.csv.

No live rates, no network calls. Policy for missing exact (date, pair)
matches, since the table only has 134 rows for 5 currencies:

1. Exact (rate_date, from, to) match.
2. Exact (rate_date, to, from) match, inverted (1 / rate).
3. Nearest available date <= the requested date for that pair (direct or
   inverted). Fixed rates are dated snapshots; the most recent known rate
   on/before the settlement date is the correct one to apply.
4. Nearest available date > the requested date, as a last resort (still
   deterministic and still drawn only from the supplied table).

All results are Decimal. Same-currency conversion always returns 1.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from .models import ExchangeRate


class ExchangeRateTable:
    def __init__(self, rates: list[ExchangeRate]) -> None:
        # index: (from, to) -> sorted list of (rate_date, rate)
        self._direct: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
        for r in rates:
            self._direct.setdefault((r.from_currency, r.to_currency), []).append((r.rate_date, r.rate))
        for lst in self._direct.values():
            lst.sort(key=lambda t: t[0])

    def _lookup_direction(self, frm: str, to: str, on: date) -> Optional[Decimal]:
        series = self._direct.get((frm, to))
        if not series:
            return None
        exact = [rate for d, rate in series if d == on]
        if exact:
            return exact[0]
        before = [t for t in series if t[0] <= on]
        if before:
            return before[-1][1]
        after = [t for t in series if t[0] > on]
        if after:
            return after[0][1]
        return None

    def get_rate(self, from_currency: str, to_currency: str, on: date) -> Decimal:
        if from_currency == to_currency:
            return Decimal("1")
        direct = self._lookup_direction(from_currency, to_currency, on)
        if direct is not None:
            return direct
        inverse = self._lookup_direction(to_currency, from_currency, on)
        if inverse is not None and inverse != 0:
            return Decimal("1") / inverse
        # No path found in the supplied table at all: fail loudly rather
        # than fabricate a rate.
        raise LookupError(f"No exchange rate available for {from_currency}->{to_currency} near {on}")

    def convert(self, amount: Decimal, from_currency: str, to_currency: str, on: date) -> Decimal:
        rate = self.get_rate(from_currency, to_currency, on)
        return (amount * rate).quantize(Decimal("0.01"))
