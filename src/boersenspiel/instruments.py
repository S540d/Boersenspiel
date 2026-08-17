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
        # Einzelaktien-Satellit (volatile Einzelwerte, siehe strategies.py
        # BARBELL_20_60_20_SATELLIT) - bewusst gemischt aus hoch-volatilen
        # Wachstumswerten und zwei defensiven Blue-Chips (Coca-Cola, Roche)
        # als Gegenbeispiel innerhalb desselben Topfs.
        Instrument("LITE", "Lumentum Holdings (Optik/Photonik)", "US55024U1097"),
        Instrument("BYDDY", "BYD Company (ADR, E-Autos)", "US05606L1008"),
        Instrument("SEDG", "SolarEdge Technologies (Solar-Wechselrichter)", "US83417M1045"),
        Instrument("S92", "SMA Solar Technology AG", "DE000A0DJ6J9"),
        Instrument("TSLA", "Tesla", "US88160R1014"),
        Instrument("PLTR", "Palantir Technologies", "US69608A1088"),
        Instrument("MSTR", "Strategy Inc. (vormals MicroStrategy)", "US5949724083"),
        Instrument("RIVN", "Rivian Automotive", "US76954A1034"),
        Instrument("KO", "Coca-Cola (defensiver Blue Chip)", "US1912161007"),
        Instrument("RHHBY", "Roche Holding (ADR, defensiver Blue Chip)", "US7711951043"),
    ]
}

# Reihenfolge bestimmt die Spaltenreihenfolge in price_history.csv.
TICKERS: list[str] = list(INSTRUMENTS.keys())
