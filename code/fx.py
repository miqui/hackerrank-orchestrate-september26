"""FX conversion per R16 / R10.

Rules (binding, verified against the shipped dataset):
- Primary match: EXACT (rate_date, from_currency, to_currency) row. On the
  shipped 140 foreign-currency events this ALWAYS succeeds.
- Fallback: nearest-PRECEDING dated rate for the same pair. This is
  defensive only — it must never fire on the shipped dataset (T11 asserts
  zero fallback hits).
- NEVER invert or chain: USD->EUR=0.92 and EUR->USD=1.09 are independent,
  non-reciprocal entries in the table. Only a listed (from, to) pair, as
  listed, may be used. Deriving a missing pair via 1/rate or via a chained
  intermediate currency is forbidden and must raise.
- Missing pair entirely (no exact match and no preceding rate for that
  pair) -> quarantine (recorded, not silently defaulted) and a loud error.
- No live/network exchange rates, ever.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from loaders import ExchangeRate


class FxError(ValueError):
    """Raised for any FX lookup that cannot be satisfied from the fixed table."""


class FxInversionError(FxError):
    """Raised if code attempts to invert or chain a rate pair."""


@dataclass(frozen=True)
class FxMatch:
    amount: Decimal
    rate: Decimal
    rate_date: date
    used_fallback: bool  # True only if exact-date match failed and nearest-preceding was used


class FxTable:
    """Fixed, dated FX rate table. Exact-match primary, nearest-preceding fallback.

    Internally keeps, per (from_currency, to_currency) pair, a sorted list of
    (rate_date, rate) entries. Lookup for a given pair+date is O(log n).
    """

    def __init__(self, rates: Iterable[ExchangeRate]):
        self._by_pair: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}
        self._exact: dict[tuple[str, str, date], Decimal] = {}
        self.quarantine: list[dict] = []
        for r in rates:
            key = (r.from_currency, r.to_currency)
            self._by_pair.setdefault(key, []).append((r.rate_date, r.rate))
            self._exact[(r.from_currency, r.to_currency, r.rate_date)] = r.rate
        for key in self._by_pair:
            self._by_pair[key].sort(key=lambda t: t[0])

    def known_pairs(self) -> set[tuple[str, str]]:
        return set(self._by_pair.keys())

    def get_rate(self, from_currency: str, to_currency: str, on_date: date,
                 context: str = "") -> tuple[Decimal, date, bool]:
        """Return (rate, rate_date_used, used_fallback). Raises FxError if unresolvable.

        NEVER inverts (from,to) into (to,from) and NEVER chains through a
        third currency. If the pair itself is not present in the table at
        all, this is a quarantine-worthy error, even if the inverse pair
        exists.
        """
        if from_currency == to_currency:
            return Decimal("1"), on_date, False

        exact = self._exact.get((from_currency, to_currency, on_date))
        if exact is not None:
            return exact, on_date, False

        pair_key = (from_currency, to_currency)
        series = self._by_pair.get(pair_key)
        if not series:
            self.quarantine.append({
                "from": from_currency, "to": to_currency, "date": on_date.isoformat(),
                "context": context, "reason": "no_rate_row_for_pair",
            })
            raise FxError(
                f"No exchange rate available for {from_currency}->{to_currency} "
                f"(requested for {on_date.isoformat()}, context={context!r}). "
                f"Inverting {to_currency}->{from_currency} is forbidden; refusing to guess."
            )

        dates = [d for d, _ in series]
        idx = bisect_right(dates, on_date) - 1
        if idx < 0:
            self.quarantine.append({
                "from": from_currency, "to": to_currency, "date": on_date.isoformat(),
                "context": context, "reason": "no_preceding_rate",
            })
            raise FxError(
                f"No rate on or before {on_date.isoformat()} for {from_currency}->{to_currency} "
                f"(context={context!r})."
            )
        rate_date, rate = series[idx]
        return rate, rate_date, True

    def convert(self, amount: Decimal, from_currency: str, to_currency: str, on_date: date,
                context: str = "") -> FxMatch:
        rate, rate_date, used_fallback = self.get_rate(from_currency, to_currency, on_date, context)
        return FxMatch(amount=amount * rate, rate=rate, rate_date=rate_date, used_fallback=used_fallback)

    def would_require_inversion(self, from_currency: str, to_currency: str) -> bool:
        """True if (from,to) is absent but the reverse (to,from) IS present.

        Used only by tests/guards to prove the code never takes the
        forbidden shortcut of inverting a rate.
        """
        return (from_currency, to_currency) not in self._by_pair and \
            (to_currency, from_currency) in self._by_pair
