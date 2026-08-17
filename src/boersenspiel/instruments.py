"""Quellenunabhängige Definition der handelbaren Instrumente.

Enthält bewusst KEIN Source-spezifisches Symbol-Mapping (z. B. yfinance- oder
Stooq-Ticker-Suffixe) - das liegt in ``sources/yfinance_stooq.py``. So bleibt
diese Datei unverändert, egal welche Kursquelle gerade aktiv ist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    isin: str | None


INSTRUMENTS: dict[str, Instrument] = {
    i.ticker: i
    for i in [
        Instrument("EUNL", "MSCI World ETF (iShares Core, thes.)", "IE00B4L5Y983"),
        Instrument("EUNA", "Global Aggregate Bond ETF (iShares Core, EUR hedged)", "IE00BDBRDM35"),
        Instrument("4GLD", "Xetra-Gold", "DE000A0S9GB0"),
        Instrument("LYMS", "Nasdaq-100 ETF (Amundi Core, synthetisch)", "LU1829221024"),
        Instrument("SEMI", "Global Semiconductors ETF (iShares)", "IE000I8KRLL9"),
        Instrument("EIMI", "EM IMI ETF (iShares Core)", "IE00BKM4GZ66"),
        Instrument("BTC-EUR", "Bitcoin", None),
    ]
}

# Reihenfolge bestimmt die Spaltenreihenfolge in price_history.csv.
TICKERS: list[str] = list(INSTRUMENTS.keys())
