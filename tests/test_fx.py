"""Golden case T11 — FX exact-match + no-inversion (data-level).

evaluation/golden_cases.md T11:
    State: the 140 foreign events + 134 rate rows as shipped.
    Expect: zero fallback hits on exact-date matching; any inversion/chain-
    derived rate fails the test; scheduled foreign income (8 rows) converts
    at settlement-date rate row.
    Exercises: R16; non-reciprocal pair trap (USD-EUR 0.92 vs EUR-USD 1.09).

Also covers the data-level slice of T12 (blank-amount foreign event
event_7307) insofar as it exercises "convert after extraction, never
assume home currency" at the FX layer (full T12 extraction-pipeline
coverage belongs to task 2.1's image-extraction tests).
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent / "code"
sys.path.insert(0, str(CODE_DIR))

import loaders  # noqa: E402
from fx import FxError, FxTable  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"


class TestFxGoldenT11(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = loaders.load_financial_profiles()
        cls.events = loaders.load_financial_events()
        cls.rates = loaders.load_exchange_rates()
        cls.fx = FxTable(cls.rates)
        cls.foreign_events = [
            ev for ev in cls.events
            if ev.currency and ev.user_id in cls.profiles
            and ev.currency != cls.profiles[ev.user_id].home_currency
        ]

    def test_140_foreign_currency_events_present(self):
        self.assertEqual(len(self.foreign_events), 140)

    def test_all_foreign_events_have_exact_date_match_zero_fallback(self):
        """(a) all 140 foreign-currency events match a rate row on the exact
        settlement/event date; zero fallback hits."""
        fallback_hits = []
        unresolved = []
        for ev in self.foreign_events:
            home_currency = self.profiles[ev.user_id].home_currency
            effective_date = ev.settlement_date or ev.event_date
            try:
                rate, rate_date, used_fallback = self.fx.get_rate(
                    ev.currency, home_currency, effective_date, context=ev.event_id
                )
            except FxError as exc:
                unresolved.append((ev.event_id, str(exc)))
                continue
            if used_fallback:
                fallback_hits.append((ev.event_id, effective_date, rate_date))

        self.assertEqual(unresolved, [], f"unresolved FX lookups: {unresolved}")
        self.assertEqual(
            fallback_hits, [],
            f"expected ZERO fallback hits on shipped data, got: {fallback_hits}"
        )

    def test_non_inversion_is_refused(self):
        """(b) deriving a rate by inverting must be caught: fx.py must never
        silently invert an existing reverse-pair rate, and USD->EUR /
        EUR->USD must be the deliberate non-reciprocal pair (0.92 vs 1.09,
        NOT 1/0.92)."""
        usd_eur_rate, _, _ = self.fx.get_rate("USD", "EUR", __import__("datetime").date(2024, 4, 15))
        eur_usd_rate, _, _ = self.fx.get_rate("EUR", "USD", __import__("datetime").date(2024, 4, 15))

        self.assertEqual(usd_eur_rate, Decimal("0.92"))
        self.assertNotEqual(eur_usd_rate, Decimal("1") / usd_eur_rate)

        inverse_of_usd_eur = Decimal("1") / usd_eur_rate
        self.assertNotAlmostEqual(float(eur_usd_rate), float(inverse_of_usd_eur), places=3)

        # If a pair is genuinely absent, get_rate must raise rather than
        # invert the reverse pair, even when the reverse pair IS known.
        known_pairs = self.fx.known_pairs()
        for (a, b) in list(known_pairs):
            reverse_missing = (b, a) not in known_pairs
            if reverse_missing:
                with self.assertRaises(FxError):
                    self.fx.get_rate(b, a, __import__("datetime").date(2023, 10, 15))
                break

    def test_scheduled_foreign_income_uses_settlement_date_rate(self):
        """(c) the 8 scheduled foreign income rows convert at the
        settlement-date rate."""
        scheduled_foreign_income = [
            ev for ev in self.foreign_events
            if ev.status == "scheduled" and ev.direction == "credit"
        ]
        self.assertEqual(len(scheduled_foreign_income), 8)

        for ev in scheduled_foreign_income:
            home_currency = self.profiles[ev.user_id].home_currency
            self.assertIsNotNone(
                ev.settlement_date,
                f"{ev.event_id}: scheduled foreign income must carry a settlement_date",
            )
            rate, rate_date, used_fallback = self.fx.get_rate(
                ev.currency, home_currency, ev.settlement_date, context=ev.event_id
            )
            self.assertEqual(rate_date, ev.settlement_date)
            self.assertFalse(used_fallback)

    def test_blank_amount_foreign_event_7307_requires_extraction_not_home_currency_guess(self):
        """T12 data-level slice: event_7307 has a blank USD amount; fx.py must
        not be asked to convert until an amount is extracted, and must not
        assume the blank means home-currency parity."""
        ev7307 = next(ev for ev in self.events if ev.event_id == "event_7307")
        self.assertIsNone(ev7307.amount, "event_7307 amount must stay blank (None), never 0")
        self.assertEqual(ev7307.currency, "USD")

        home_currency = self.profiles[ev7307.user_id].home_currency
        self.assertNotEqual(home_currency, "USD")
        # The correct flow is: extract amount from the linked image FIRST,
        # then convert. Converting a bare 0 (i.e. treating the blank as 0)
        # would silently succeed here since USD->INR exists — which is
        # exactly the trap: reconstruct.normalize_event refuses to proceed
        # without an amount override for a blank-amount event.
        from reconstruct import ReconstructError, normalize_event
        with self.assertRaises(ReconstructError):
            normalize_event(ev7307, self.profiles[ev7307.user_id], self.fx)


class TestFxMiscHardening(unittest.TestCase):
    def test_same_currency_is_identity_no_table_lookup(self):
        fx = FxTable(loaders.load_exchange_rates())
        m = fx.convert(Decimal("100"), "USD", "USD", __import__("datetime").date(2023, 10, 15))
        self.assertEqual(m.amount, Decimal("100"))
        self.assertFalse(m.used_fallback)

    def test_missing_pair_raises_and_quarantines(self):
        fx = FxTable(loaders.load_exchange_rates())
        with self.assertRaises(FxError):
            fx.convert(Decimal("1"), "GBP", "JPY", __import__("datetime").date(2023, 10, 15), context="unit-test")
        self.assertTrue(any(q["from"] == "GBP" for q in fx.quarantine))


if __name__ == "__main__":
    unittest.main()
