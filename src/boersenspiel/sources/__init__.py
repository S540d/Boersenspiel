"""Austauschbare Kursquellen.

Jede Quelle implementiert das ``PriceSource``-Protokoll: sie bekommt die
Liste der Ticker (aus ``instruments.py``) sowie ein "as_of"-Datum und liefert
für jeden Ticker entweder einen Kurs (Status ``ok``) oder signalisiert, dass
kein Kurs gefunden wurde (Status ``missing``) - Letzteres lässt
``history_store.record_week`` dann automatisch auf den letzten bekannten Kurs
zurückfallen ("carry forward"). Status ``rate_limited`` ist ein Sonderfall von
``missing``: die Quelle hat erkennbar ein Rate-Limit erreicht statt keinen
Kurs für dieses Instrument zu haben - ``record_week`` behandelt beide gleich
(carry forward), vermerkt den Unterschied aber im ``fetch_log.csv``.

Die konkrete Quelle ist bewusst austauschbar: aktiver Standardweg ist
``AlphaVantageSource`` über ``scripts/run_fetch.py`` (GitHub Actions, siehe
#51 - der frühere manuelle Cowork-Weg über ``record_prices.py`` wurde
gestrichen, der Kursabruf läuft konsequent über GitHub Actions). Engine,
Dashboard und Tests wissen dabei nichts davon, woher die Zahlen kamen.
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
    status: str  # "ok" | "missing" | "rate_limited"
    source: str = ""
    # Handelstag, auf den sich ``price`` bezieht - NICHT der Tag des Abrufs.
    # Ein Montagslauf liefert vor Börsenbeginn den Freitagsschluss; ohne diese
    # Angabe landet der Kurs in der falschen Kalenderwoche (siehe
    # ``scripts/run_fetch.py``). Quellen, die keinen Handelstag mitliefern,
    # lassen das Feld None - dann bleibt das übergebene Abrufdatum maßgeblich.
    quote_date: date | None = None


class PriceSource(Protocol):
    """Schnittstelle, die jede Kursquelle erfüllen muss."""

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, PriceQuote]:
        """Liefert für jeden angefragten Ticker ein PriceQuote."""
        ...
