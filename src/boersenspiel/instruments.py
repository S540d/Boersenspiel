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
        # --- Sieben zusaetzliche Instrumente (#64) ------------------------------
        # Ergaenzt am 22.08.2026, um das taegliche Alpha-Vantage-Budget von 25
        # Requests auszuschoepfen (vorher 18). Alle sieben sind XETRA-Symbole in
        # EUR, kosten also keinen zusaetzlichen FX-Request.
        #
        # Steuerattribute verifiziert: `teilfreistellung`/`thesaurierend`/
        # `ausschuettend` wurden gegen oeffentliche Fondsanbieter-Fact-Sheets
        # (justETF/extraETF/onvista/DAS INVESTMENT) abgeglichen, nicht nur aus
        # Fondsgattung/Namenszusatz geraten. Dabei zwei Fehler gefunden und
        # korrigiert: IBCI und EXXY sind tatsaechlich thesaurierende
        # Acc-Anteilsklassen, waren aber faelschlich als ausschuettend markiert.
        #
        # IUSA dient ausschliesslich als Benchmark (`SP500_BENCHMARK` in
        # strategies.py, 100% Einzelinstrument, nie rebalanciert) - es ist
        # NICHT zusaetzlich Bestandteil einer der Barbell-Topf-Allokationen.
        # Die uebrigen sechs (XEON, EXSA, IBCL, IBCI, IQQ6, EXXY) sind Teil von
        # `BARBELL_20_80_DIVERSIFIZIERT`.
        Instrument(
            "IUSA",
            "S&P 500 ETF (iShares Core, aussch.) - nur Benchmark",
            "IE0031442068",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            ausschuettend=True,
        ),
        Instrument(
            "XEON",
            "EUR-Geldmarkt ETF (Xtrackers II Overnight Rate, thes.)",
            "LU0290358497",
            # Kein Aktienfonds -> keine Teilfreistellung.
            thesaurierend=True,
        ),
        Instrument(
            "EXSA",
            "STOXX Europe 600 ETF (iShares, aussch.)",
            "DE0002635307",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            ausschuettend=True,
        ),
        Instrument(
            "IBCL",
            "Euro-Staatsanleihen 15-30 Jahre ETF (iShares, aussch.)",
            "IE00B1FZS913",
            ausschuettend=True,
        ),
        Instrument(
            "IBCI",
            "Inflationsindexierte Euro-Staatsanleihen ETF (iShares, thes.)",
            "IE00B0M62X26",
            # Acc-Anteilsklasse (WKN A0HGV1), nicht Dist - siehe Verifikations-
            # Hinweis oben.
            thesaurierend=True,
        ),
        Instrument(
            "IQQ6",
            "Immobilien-ETF (iShares Developed Markets Property Yield, aussch.)",
            "IE00B1FZS350",
            # Haelt boersennotierte Immobiliengesellschaften/REITs, also >51%
            # Aktien -> Aktienfonds-Teilfreistellung, nicht die Immobilienfonds-
            # Quote.
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            ausschuettend=True,
        ),
        Instrument(
            "EXXY",
            "Rohstoff-ETF breit (iShares Diversified Commodity Swap, thes.)",
            "DE000A0H0728",
            # Rohstofffonds -> keine Teilfreistellung. Acc-Anteilsklasse, nicht
            # Dist - siehe Verifikations-Hinweis oben.
            thesaurierend=True,
        ),
    ]
}

# Reihenfolge bestimmt die Spaltenreihenfolge in price_history.csv.
TICKERS: list[str] = list(INSTRUMENTS.keys())
