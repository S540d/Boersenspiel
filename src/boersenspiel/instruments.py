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
    # True für Instrumente, die reale Bar-Ausschüttungen zahlen (#57). 4GLD und
    # BTC-EUR zahlen keine, thesaurierende Fonds schütten definitionsgemäß nicht
    # aus - dort bleibt es beim Default False. Ebenso bei Einzelaktien, die
    # tatsächlich keine Dividende zahlen (Tesla, Rivian, Palantir, MSTR,
    # SolarEdge, Lumentum, SMA Solar) - siehe #74.
    ausschuettend: bool = False
    # Erwartete jährliche Ausschüttungsrendite dieses Instruments (#74). Wirkt
    # nur bei ``ausschuettend=True``. ``None`` bedeutet "kein instrumenteneigener
    # Wert hinterlegt" und fällt in der Engine auf
    # ``strategies.DIVIDENDENRENDITE_PLATZHALTER`` zurück.
    #
    # Vorher galt dieser eine Pauschalwert (2,5%) für ALLE ausschüttenden
    # Instrumente gleichermaßen. Das war nicht bloß ungenau, sondern gerichtet
    # verzerrend: sieben Einzelaktien, die real gar keine Dividende zahlen,
    # bekamen jährlich 2,5% geschenkt, während Anleihen- und Immobilien-ETFs -
    # deren Ertrag zum großen Teil GERADE aus der Ausschüttung besteht - zu
    # niedrig angesetzt waren. Die Pauschale begünstigte damit systematisch
    # ausschüttungslose Wachstumswerte gegenüber genau den defensiven
    # Bausteinen, die eine Barbell-Strategie überhaupt rechtfertigen.
    #
    # Die hinterlegten Werte sind gerundete Ausschüttungsrenditen aus
    # öffentlichen Fondsanbieter-Fact-Sheets bzw. Unternehmensangaben
    # (justETF/extraETF/onvista, Stand 2026) und bleiben eine Annahme: die
    # Simulation rechnet mit einem über die ganze Historie KONSTANTEN Satz je
    # Instrument, nicht mit den tatsächlichen Ausschüttungen des jeweiligen
    # Jahres. Das ist die verbleibende Vereinfachung - aber eine ungerichtete,
    # anders als ein einziger Satz für alle.
    dividendenrendite: Decimal | None = None
    # Laufende Fondskosten (Total Expense Ratio) p.a. (#76). 0 fuer Einzelaktien,
    # physisches Gold und BTC - dort faellt keine Fondsgebuehr an.
    #
    # Der Verzicht auf die TER war kein neutraler Verzicht, sondern eine gerichtete
    # Verzerrung: die Saetze liegen um eine Groessenordnung auseinander (IUSA 0,07%
    # gegen IQQ6 0,59%), der Benchmark ist das mit Abstand guenstigste Instrument im
    # Feld, und der Einzelaktien-Satellit traegt gar keine - die Modellierung
    # beguenstigte also ausgerechnet die konzentrierteste Variante und liess
    # Themen-/Nischenprodukte guenstiger erscheinen, als sie sind.
    #
    # Werte gerundet aus den jeweiligen Anbieter-Fact-Sheets.
    ter: Decimal = Decimal("0")


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
            ter=Decimal("0.0020"),
        ),
        Instrument(
            "EUNA",
            "Global Aggregate Bond ETF (iShares Core, EUR hedged)",
            "IE00BDBRDM35",
            # Rentenfonds - keine Teilfreistellung (nur Aktienfonds).
            thesaurierend=True,
            ter=Decimal("0.0010"),
        ),
        Instrument("4GLD", "Xetra-Gold", "DE000A0S9GB0"),
        Instrument(
            "LYMS",
            "Nasdaq-100 ETF (Amundi Core, synthetisch)",
            "LU1829221024",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
            ter=Decimal("0.0023"),
        ),
        Instrument(
            "SEMI",
            "Global Semiconductors ETF (iShares)",
            "IE000I8KRLL9",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
            ter=Decimal("0.0035"),
        ),
        Instrument(
            "EIMI",
            "EM IMI ETF (iShares Core)",
            "IE00BKM4GZ66",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            thesaurierend=True,
            ter=Decimal("0.0018"),
        ),
        Instrument("BTC-EUR", "Bitcoin", None, spekulationsfrist_tage=365),
        # Einzelaktien-Satellit (volatile Einzelwerte, siehe strategies.py
        # BARBELL_20_60_20_SATELLIT) - bewusst gemischt aus hoch-volatilen
        # Wachstumswerten und zwei defensiven Blue-Chips (Coca-Cola, Roche)
        # als Gegenbeispiel innerhalb desselben Topfs.
        # Zahlt keine Dividende (#74).
        Instrument("LITE", "Lumentum Holdings (Optik/Photonik)", "US55024U1097"),
        Instrument(
            "BYDDY",
            "BYD Company (ADR, E-Autos)",
            "US05606L1008",
            ausschuettend=True,
            dividendenrendite=Decimal("0.010"),
        ),
        # Zahlt keine Dividende (#74).
        Instrument("SEDG", "SolarEdge Technologies (Solar-Wechselrichter)", "US83417M1045"),
        # Dividende ausgesetzt - keine laufende Ausschuettung (#74).
        Instrument("S92", "SMA Solar Technology AG", "DE000A0DJ6J9"),
        # Zahlt keine Dividende (#74).
        Instrument("TSLA", "Tesla", "US88160R1014"),
        # Zahlt keine Dividende (#74).
        Instrument("PLTR", "Palantir Technologies", "US69608A1088"),
        # Keine Dividende auf die Stammaktie (#74).
        Instrument("MSTR", "Strategy Inc. (vormals MicroStrategy)", "US5949724083"),
        # Zahlt keine Dividende (#74).
        Instrument("RIVN", "Rivian Automotive", "US76954A1034"),
        Instrument(
            "KO",
            "Coca-Cola (defensiver Blue Chip)",
            "US1912161007",
            ausschuettend=True,
            dividendenrendite=Decimal("0.030"),
        ),
        Instrument(
            "RHHBY",
            "Roche Holding (ADR, defensiver Blue Chip)",
            "US7711951043",
            ausschuettend=True,
            dividendenrendite=Decimal("0.033"),
        ),
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
            # S&P 500 - niedrige Ausschuettungsrendite, siehe #74. Relevant weit
            # ueber dieses eine Instrument hinaus: IUSA ist die Vergleichslinie,
            # an der alle Strategien gemessen werden.
            dividendenrendite=Decimal("0.013"),
            ter=Decimal("0.0007"),
        ),
        Instrument(
            "XEON",
            "EUR-Geldmarkt ETF (Xtrackers II Overnight Rate, thes.)",
            "LU0290358497",
            # Kein Aktienfonds -> keine Teilfreistellung.
            thesaurierend=True,
            ter=Decimal("0.0010"),
        ),
        Instrument(
            "EXSA",
            "STOXX Europe 600 ETF (iShares, aussch.)",
            "DE0002635307",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            ausschuettend=True,
            dividendenrendite=Decimal("0.030"),
            ter=Decimal("0.0020"),
        ),
        Instrument(
            "IBCL",
            "Euro-Staatsanleihen 15-30 Jahre ETF (iShares, aussch.)",
            "IE00B1FZS913",
            ausschuettend=True,
            # Lange Euro-Staatsanleihen: der Kupon IST hier praktisch der
            # gesamte laufende Ertrag (#74).
            dividendenrendite=Decimal("0.026"),
            ter=Decimal("0.0020"),
        ),
        Instrument(
            "IBCI",
            "Inflationsindexierte Euro-Staatsanleihen ETF (iShares, thes.)",
            "IE00B0M62X26",
            # Acc-Anteilsklasse (WKN A0HGV1), nicht Dist - siehe Verifikations-
            # Hinweis oben.
            thesaurierend=True,
            ter=Decimal("0.0009"),
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
            # REITs schuetten den Grossteil ihrer Ertraege aus (#74).
            dividendenrendite=Decimal("0.035"),
            ter=Decimal("0.0059"),
        ),
        Instrument(
            "EXXY",
            "Rohstoff-ETF breit (iShares Diversified Commodity Swap, thes.)",
            "DE000A0H0728",
            # Rohstofffonds -> keine Teilfreistellung. Acc-Anteilsklasse, nicht
            # Dist - siehe Verifikations-Hinweis oben.
            thesaurierend=True,
            ter=Decimal("0.0046"),
        ),
        # --- Dividende und Value (#99) ------------------------------------------
        # Zwei Faktor-/Stil-Bausteine, die es im bisherigen Instrumentenset gar
        # nicht gab: eine gezielte Dividenden- und eine gezielte
        # Value-Ausrichtung. `Instrument.dividendenrendite` (#74) modelliert nur
        # die Ausschuettung der ohnehin gehaltenen Instrumente - eine
        # Dividendenstrategie ist etwas anderes als ein hoch ausschuettendes
        # Instrument in einem beliebigen Topf.
        #
        # Beide bewusst als XETRA-Symbol in EUR (Konvention seit #64): kein
        # zusaetzlicher FX-Request, und das Waehrungsproblem aus #62 entsteht
        # fuer sie gar nicht erst. Am 28.08.2026 per SYMBOL_SEARCH/GLOBAL_QUOTE
        # geprueft - beide loesen auf XETRA in EUR auf.
        #
        # Der im Issue vorgeschlagene Value-Ticker `IUVL` existiert bei Alpha
        # Vantage NICHT (SYMBOL_SEARCH liefert keinen Treffer); die
        # EUR-/XETRA-Notierung desselben iShares-Fonds laeuft unter `IS3S.DEX`
        # (die LSE-Notierung `IWVL.LON` waere USD und damit FX-pflichtig).
        # Entsprechend ist auch die ISIN die der tatsaechlich an der XETRA
        # gehandelten Acc-Anteilsklasse (IE00BP3QZB59), nicht die im Issue
        # vorgeschlagene.
        #
        # Historie (per TIME_SERIES_MONTHLY geprueft): ISPA ab 2009-11, IS3S ab
        # 2014-11. Value-Faktor-ETFs sind wie im Issue vermutet juenger als die
        # uebrigen Instrumente - die Strategie startet nach der F4-Regel
        # (#63, _real_investierbarer_zeitraum) deshalb fruehestens Ende 2014.
        Instrument(
            "ISPA",
            "Dividenden-ETF global (iShares STOXX Global Select Dividend 100, aussch.)",
            "DE000A0F5UH1",
            # Haelt 100 dividendenstarke Aktien -> Aktienfonds-Teilfreistellung.
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            ausschuettend=True,
            # Der ganze Zweck des Fonds: eine deutlich ueber dem Markt liegende
            # Ausschuettung (justETF/extraETF, Stand 2026 rund 5%). Bei einer
            # Dividendenstrategie ist das kein Nebeneffekt, sondern der
            # wesentliche Teil des Gesamtertrags - der Platzhalter aus #74 waere
            # hier besonders irrefuehrend.
            dividendenrendite=Decimal("0.050"),
            ter=Decimal("0.0046"),
        ),
        Instrument(
            "IS3S",
            "Value-Faktor-ETF (iShares Edge MSCI World Value Factor, thes.)",
            "IE00BP3QZB59",
            teilfreistellung=_TEILFREISTELLUNG_AKTIENFONDS,
            # USD (Acc)-Anteilsklasse, an der XETRA in EUR gehandelt.
            thesaurierend=True,
            ter=Decimal("0.0030"),
        ),
    ]
}

# Reihenfolge bestimmt die Spaltenreihenfolge in price_history.csv.
TICKERS: list[str] = list(INSTRUMENTS.keys())
