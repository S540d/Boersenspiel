"""Quellenunabhängige Definition der handelbaren Instrumente.

Enthält bewusst KEIN Source-spezifisches Symbol-Mapping (z. B. yfinance- oder
Stooq-Ticker-Suffixe) - das liegt in ``sources/yfinance_stooq.py``. So bleibt
diese Datei unverändert, egal welche Kursquelle gerade aktiv ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    ticker: str
    name: str
    isin: str | None
    # Teilfreistellungsquote nach § 20 InvStG (30% für Aktienfonds mit >51%
    # Aktienquote, 0% für Renten-/Misch-/Rohstofffonds, physische Edelmetalle
    # und Einzelaktien, die keinem Fondsprivileg unterliegen). Siehe #38.
    teilfreistellung: Decimal = Decimal("0")
    # Thesaurierend (True) -> unterliegt der jährlichen Vorabpauschale (#39).
    # Bewusst als Annahme markiert, wo die Ausschüttungsart nicht am Namen
    # erkennbar ist (siehe #39-Kommentar "vermutlich auch ...") - noch nicht
    # gegen den Fondsprospekt verifiziert.
    thesaurierend: bool = False
    # Gesetzt (Anzahl Tage), wenn das Instrument statt der Abgeltungsteuer der
    # Spekulationsfrist für private Veräußerungsgeschäfte (§ 23 EStG)
    # unterliegt - aktuell nur Kryptowährungen (365 Tage). None = normales
    # Abgeltungsteuer-Regime. Siehe #37.
    spekulationsfrist_tage: int | None = None
    # True für Instrumente, die reale Bar-Ausschüttungen zahlen (Einzelaktien) -
    # unterliegt in der Simulation der pauschalen Dividendenrendite-Annahme
    # (`strategies.DIVIDENDENRENDITE_PLATZHALTER`, #57). Die 5 ETFs sind
    # thesaurierend (keine Ausschüttung, siehe oben), 4GLD und BTC-EUR zahlen
    # keine Dividende - bei allen dreien bleibt es beim Default False.
    ausschuettend: bool = False


# Teilfreistellungsquote für Aktienfonds-ETFs (>51% Aktienquote) nach § 20 InvStG.
_TEILFREISTELLUNG_AKTIENFONDS = Decimal("0.30")

INSTRUMENTS: dict[str, Instrument] = {
    i.ticker: i
    for i in [
        Instrument(
            "EUNL",
            "MSCI World ETF (iShares Core, thes.)",
            "IE00B4L5Y983",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
        ),
        Instrument(
            "EUNA",
            "Global Aggregate Bond ETF (iShares Core, EUR hedged)",
            "IE00BDBRDM35",
            # Rentenfonds - keine Teilfreistellung (nur Aktienfonds).
            thesaurierend=True,
        ),
        Instrument("4GLD", "Xetra-Gold", "DE000A0S9GB0"),
        Instrument(
            "LYMS",
            "Nasdaq-100 ETF (Amundi Core, synthetisch)",
            "LU1829221024",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
        ),
        Instrument(
            "SEMI",
            "Global Semiconductors ETF (iShares)",
            "IE000I8KRLL9",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
        ),
        Instrument(
            "EIMI",
            "EM IMI ETF (iShares Core)",
            "IE00BKM4GZ66",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
        ),
        Instrument("BTC-EUR", "Bitcoin", None, spekulationsfrist_tage=365),
        # Einzelaktien-Satellit (volatile Einzelwerte, siehe strategies.py
        # BARBELL_20_60_20_SATELLIT) - bewusst gemischt aus hoch-volatilen
        # Wachstumswerten und zwei defensiven Blue-Chips (Coca-Cola, Roche)
        # als Gegenbeispiel innerhalb desselben Topfs.
        Instrument("LITE", "Lumentum Holdings (Optik/Photonik)", "US55024U1097", ausschuettend=True),
        Instrument("BYDDY", "BYD Company (ADR, E-Autos)", "US05606L1008", ausschuettend=True),
        Instrument("SEDG", "SolarEdge Technologies (Solar-Wechselrichter)", "US83417M1045", ausschuettend=True),
        Instrument("S92", "SMA Solar Technology AG", "DE000A0DJ6J9", ausschuettend=True),
        Instrument("TSLA", "Tesla", "US88160R1014", ausschuettend=True),
        Instrument("PLTR", "Palantir Technologies", "US69608A1088", ausschuettend=True),
        Instrument("MSTR", "Strategy Inc. (vormals MicroStrategy)", "US5949724083", ausschuettend=True),
        Instrument("RIVN", "Rivian Automotive", "US76954A1034", ausschuettend=True),
        Instrument("KO", "Coca-Cola (defensiver Blue Chip)", "US1912161007", ausschuettend=True),
        Instrument("RHHBY", "Roche Holding (ADR, defensiver Blue Chip)", "US7711951043", ausschuettend=True),
    ]
}

# Reihenfolge bestimmt die Spaltenreihenfolge in price_history.csv.
TICKERS: list[str] = list(INSTRUMENTS.keys())
