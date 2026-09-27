"""Gemeinsame Test-Fixtures über mehrere Testdateien hinweg."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.history_store import PriceRow


def simple_rows() -> list[PriceRow]:
    """Zwei Wochen, ein einziges Instrument, Kurs steigt von 100 auf 150 -
    kleinste Fixture, die eine End-to-End-Simulation ermöglicht."""
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150")}),
    ]
