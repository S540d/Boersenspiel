"""Austauschbare Kursquellen.

Jede Quelle implementiert das ``PriceSource``-Protokoll: sie bekommt die
Liste der Ticker (aus ``instruments.py``) sowie ein "as_of"-Datum und liefert
für jeden Ticker entweder einen Kurs (Status ``ok``) oder signalisiert, dass
kein Kurs gefunden wurde (Status ``missing``) - Letzteres lässt
``history_store.record_week`` dann automatisch auf den letzten bekannten Kurs
zurückfallen ("carry forward").

Die konkrete Quelle ist bewusst austauschbar: der GitHub-Actions-Workflow
nutzt standardmäßig ``yfinance_stooq.YfinanceStooqSource``, aber der Kursabruf
kann genauso gut manuell (z. B. per Cowork-Websuche) erfolgen und die
ermittelten Kurse direkt über ``scripts/record_prices.py`` an
``history_store.record_week`` übergeben - ohne dass Engine, Dashboard oder
Tests davon wissen müssen, woher die Zahlen kamen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class PriceQuote:
    """Ergebnis eines Kursabrufs für ein einzelnes Instrument."""

    ticker: str
    price: float | None
    status: str  # "ok" oder "missing"
    source: str = ""


class PriceSource(Protocol):
    """Schnittstelle, die jede Kursquelle erfüllen muss."""

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, PriceQuote]:
        """Liefert für jeden angefragten Ticker ein PriceQuote."""
        ...
