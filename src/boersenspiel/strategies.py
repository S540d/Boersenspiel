"""Austauschbare Strategie-Definitionen.

Die Engine (``engine.py``) enthält keinerlei fest einprogrammierte Gewichte
oder Schwellenwerte - jede Strategie wird als ``Strategy``-Instanz übergeben.
Neue Strategien werden einfach als weiterer Eintrag in ``STRATEGIES`` ergänzt,
ohne Engine-Code anzufassen.

Steuer- und Gebührenkonstanten gelten strategieübergreifend identisch (aus dem
Pflichtenheft unverändert übernommen) und liegen deshalb ebenfalls hier statt
in der einzelnen Strategie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .history_store import PriceRow


@dataclass(frozen=True)
class Topf:
    """Ein Anlage-Topf (z. B. 'Sicherheit' oder 'Wachstum') mit Sub-Gewichten."""

    name: str
    gewicht_gesamt: Decimal  # Anteil dieses Topfs am Gesamtdepot, z. B. Decimal("0.20")
    sub_gewichte: dict[str, Decimal] = field(default_factory=dict)  # Ticker -> Anteil INNERHALB des Topfs, summiert zu 1

    def gewicht_am_gesamtdepot(self, ticker: str) -> Decimal:
        return self.gewicht_gesamt * self.sub_gewichte[ticker]


@dataclass(frozen=True)
class Beitrag:
    """Eine Teilregel einer zusammengesetzten Strategie, deren Effekt einzeln
    ausgewiesen werden soll.

    ``ohne`` ist dieselbe Strategie mit genau dieser einen Teilregel entfernt.
    Die Darstellungsschicht misst den Effekt der Teilregel per
    "Leave-one-out": Rendite(voll) - Rendite(ohne diese Teilregel), also der
    Renditebeitrag in Prozentpunkten, den das Weglassen dieser Regel kosten
    (positiv) oder sparen (negativ) würde. Das ist bewusst nur eine
    *marginale* Betrachtung - die Beiträge summieren sich bei sich gegenseitig
    beeinflussenden Regeln nicht exakt zur Gesamtrendite auf.
    """

    name: str  # Anzeigename der Teilregel (z. B. die Börsenweisheit selbst)
    ohne: "Strategy"


@dataclass(frozen=True)
class Optimierungen:
    """Vier strategieübergreifende Simulationsmechanismen, die ``engine.simulate()``
    unabhängig von der jeweiligen Gewichtungsregel anwendet. Einzeln ein-/ausschaltbar,
    damit ihr isolierter Renditebeitrag messbar wird (siehe #17), statt nur geglaubt zu
    werden. Defaults erhalten das bisherige Verhalten exakt - eine Strategie ohne
    explizit gesetztes ``optimierungen``-Feld verhält sich wie vor Einführung dieser
    Schalter."""

    steueroptimierung: bool = True  # Dezember-Harvest (Freibetrag-Gewinnmitnahme / Tax-Loss-Harvest)
    rebalancing: bool = True  # periodische Rückführung auf die Zielgewichte
    ordergebuehren: bool = True  # False -> gebührenfreie Referenzrechnung
    besteuerung: bool = True  # False -> realisierte Gewinne fließen nicht in Freibetrag/Steuer-Tracking


@dataclass(frozen=True)
class Strategy:
    name: str
    startkapital: Decimal
    toepfe: list[Topf]
    ziel_topf: str  # Name des Topfs, dessen Gewicht am Gesamtdepot überwacht wird (Rebalancing-Trigger)
    ziel_gewicht: Decimal  # Zielgewicht dieses Topfs, z. B. Decimal("0.20")
    rebalancing_schwelle_pp: Decimal  # Abweichung in Prozentpunkten, ab der rebalanciert wird
    # Optional: macht die Ziel-Gewichte zeitabhängig statt konstant (z. B. saisonale
    # Regeln oder charttechnische Signale wie "Sell in May" / SMA-Crossover). Bekommt
    # die komplette (chronologisch sortierte) Kurshistorie plus den Index der aktuellen
    # Zeile und liefert die Ziel-Gewichte am Gesamtdepot für GENAU diese Zeile - darf
    # dabei nur auf rows[:i+1] zugreifen, um kein "Blick in die Zukunft" (Lookahead-Bias)
    # einzubauen. None bedeutet: konstante Gewichte aus den toepfe/sub_gewichte (Barbell-
    # Rebalancing-Verhalten, wie bisher).
    gewichte_fn: Callable[[list["PriceRow"], int], dict[str, Decimal]] | None = None
    # Optional: Teilregeln einer zusammengesetzten Strategie, deren Einzeleffekt das
    # Dashboard per Leave-one-out ausweist (siehe ``Beitrag``). Leer bedeutet: die
    # Strategie wird nur als Ganzes betrachtet. Die in ``Beitrag.ohne`` hinterlegten
    # Varianten haben ihrerseits keine ``beitraege`` - sonst würde die Auswertung
    # rekursiv.
    beitraege: tuple[Beitrag, ...] = ()
    # Optional: Kurzbeschreibung der Strategie/des Szenarios fürs Dashboard (#26).
    # Leer bedeutet: keine Beschreibung wird angezeigt.
    beschreibung: str = ""
    # Welche der vier Mechanismen aus ``Optimierungen`` für diese Strategie standardmäßig
    # greifen. ``engine.simulate()`` übernimmt diese, sofern ihr nicht explizit eine
    # andere ``Optimierungen``-Instanz übergeben wird (siehe #17).
    optimierungen: Optimierungen = field(default_factory=Optimierungen)
    # Optional: Name einer anderen Strategie/eines anderen Szenarios, dessen
    # Unterszenario dieses hier ist (#30) - z. B. tragen die fünf einzelnen
    # Börsenweisheiten-Szenarien den Namen von "Börsenweisheiten (alle fünf
    # kombiniert)". Rein deklarativ fürs Dashboard (gruppierte
    # Vergleichs-Charts, siehe ``dashboard._boersenweisheiten_gruppe()``) -
    # ändert nichts an der Simulation selbst, jedes Unterszenario bleibt eine
    # vollständig eigenständige ``Strategy``.
    teil_von: str | None = None

    def alle_ticker_gewichte(self) -> dict[str, Decimal]:
        """Ziel-Gewicht jedes Instruments am Gesamtdepot."""
        gewichte: dict[str, Decimal] = {}
        for topf in self.toepfe:
            for ticker in topf.sub_gewichte:
                gewichte[ticker] = topf.gewicht_am_gesamtdepot(ticker)
        return gewichte

    def topf_von(self, ticker: str) -> Topf:
        for topf in self.toepfe:
            if ticker in topf.sub_gewichte:
                return topf
        raise KeyError(f"Kein Topf für Ticker {ticker!r} in Strategie {self.name!r}")


# --- Strategie 1: Barbell 20/80 (Pflichtenheft v2.0) -----------------------

BARBELL_20_80 = Strategy(
    name="Barbell 20/80",
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Sicherheit",
            gewicht_gesamt=Decimal("0.20"),
            sub_gewichte={
                "EUNL": Decimal("0.50"),
                "EUNA": Decimal("0.35"),
                "4GLD": Decimal("0.15"),
            },
        ),
        Topf(
            name="Topf B - Wachstum",
            gewicht_gesamt=Decimal("0.80"),
            sub_gewichte={
                "LYMS": Decimal("0.40"),
                "SEMI": Decimal("0.30"),
                "EIMI": Decimal("0.20"),
                "BTC-EUR": Decimal("0.10"),
            },
        ),
    ],
    ziel_topf="Topf A - Sicherheit",
    ziel_gewicht=Decimal("0.20"),
    rebalancing_schwelle_pp=Decimal("10"),
    beschreibung=(
        "20% Sicherheit (breite Anleihen/Gold-ETFs), 80% Wachstum (breite Aktien-ETFs "
        "plus Bitcoin). Rebalancing auf die Zielgewichte, sobald der Sicherheits-Topf "
        "um mehr als 10 Prozentpunkte abweicht."
    ),
)

# --- Strategie 2: Barbell 30/70 (Beispiel für eine alternative Gewichtung) -

BARBELL_30_70 = Strategy(
    name="Barbell 30/70",
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Sicherheit",
            gewicht_gesamt=Decimal("0.30"),
            sub_gewichte={
                "EUNL": Decimal("0.50"),
                "EUNA": Decimal("0.35"),
                "4GLD": Decimal("0.15"),
            },
        ),
        Topf(
            name="Topf B - Wachstum",
            gewicht_gesamt=Decimal("0.70"),
            sub_gewichte={
                "LYMS": Decimal("0.40"),
                "SEMI": Decimal("0.30"),
                "EIMI": Decimal("0.20"),
                "BTC-EUR": Decimal("0.10"),
            },
        ),
    ],
    ziel_topf="Topf A - Sicherheit",
    ziel_gewicht=Decimal("0.30"),
    rebalancing_schwelle_pp=Decimal("15"),
    beschreibung=(
        "Defensivere Variante des Barbell-Ansatzes: 30% Sicherheit statt 20%, dafür "
        "70% Wachstum. Größere Rebalancing-Schwelle (15 statt 10 Prozentpunkte), weil "
        "der breitere Sicherheits-Topf natürlicherweise stärker schwankt."
    ),
)

# --- Strategie 3: Barbell 20/60/20 + Einzelaktien-Satellit -----------------
#
# Erweitert Barbell 20/80 um einen dritten Topf mit 10 volatilen
# Einzelaktien (statt breiter ETFs) als Satelliten-Beimischung. Topf A
# (Sicherheit) bleibt bei 20% unveraendert; der bisherige Wachstums-Topf
# (breite ETFs/BTC) wird von 80% auf 60% reduziert, die freiwerdenden 20%
# gehen 1:1 in den neuen Einzelaktien-Topf - das Gesamtrisikoprofil (80%
# "riskant" vs. 20% "sicher") bleibt damit wie beim Original-Barbell
# erhalten, nur granularer gestreut. Die 10 Einzelaktien sind bewusst
# gleichgewichtet (je 10% des Topfs) und mischen hoch-volatile Wachstums-
# /Themenwerte mit zwei defensiven Blue Chips (Coca-Cola, Roche) als
# Gegenbeispiel - kein Optimierungsziel, sondern Illustrationszweck fuer den
# Renditevergleich mit den reinen ETF-Strategien.

BARBELL_20_60_20_SATELLIT = Strategy(
    name="Barbell 20/60/20 + Einzelaktien-Satellit",
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Sicherheit",
            gewicht_gesamt=Decimal("0.20"),
            sub_gewichte={
                "EUNL": Decimal("0.50"),
                "EUNA": Decimal("0.35"),
                "4GLD": Decimal("0.15"),
            },
        ),
        Topf(
            name="Topf B - Wachstum",
            gewicht_gesamt=Decimal("0.60"),
            sub_gewichte={
                "LYMS": Decimal("0.40"),
                "SEMI": Decimal("0.30"),
                "EIMI": Decimal("0.20"),
                "BTC-EUR": Decimal("0.10"),
            },
        ),
        Topf(
            name="Topf C - Einzelaktien-Satellit",
            gewicht_gesamt=Decimal("0.20"),
            sub_gewichte={
                "LITE": Decimal("0.10"),
                "BYDDY": Decimal("0.10"),
                "SEDG": Decimal("0.10"),
                "S92": Decimal("0.10"),
                "TSLA": Decimal("0.10"),
                "PLTR": Decimal("0.10"),
                "MSTR": Decimal("0.10"),
                "RIVN": Decimal("0.10"),
                "KO": Decimal("0.10"),
                "RHHBY": Decimal("0.10"),
            },
        ),
    ],
    ziel_topf="Topf A - Sicherheit",
    ziel_gewicht=Decimal("0.20"),
    rebalancing_schwelle_pp=Decimal("10"),
    beschreibung=(
        "Wie Barbell 20/80, aber der Wachstums-Topf sinkt von 80% auf 60% zugunsten "
        "eines dritten, gleichgewichteten Topfs aus 10 Einzelaktien (20%) - das "
        "80/20-Risikoprofil bleibt erhalten, nur granularer gestreut."
    ),
)

STRATEGIES: list[Strategy] = [BARBELL_20_80, BARBELL_30_70, BARBELL_20_60_20_SATELLIT]

STRATEGIES_BY_NAME: dict[str, Strategy] = {s.name: s for s in STRATEGIES}


# --- Steuer- und Gebührenkonstanten (strategieübergreifend, aus dem Pflichtenheft) --

ORDERGEBUEHR = Decimal("1")  # Euro pro Trade (Kauf wie Verkauf)
STEUERSATZ = Decimal("0.26375")  # 25% Kapitalertragsteuer + 5,5% Soli darauf
SPARERPAUSCHBETRAG_PRO_JAHR = Decimal("1000")  # Euro, Reset zu Jahresbeginn

# --- Vorabpauschale für thesaurierende Fonds (#39) --------------------------
#
# TODO(#39): Platzhalter-Vereinfachung. Der tatsächliche Basiszins wird vom
# BMF jährlich neu bekanntgegeben (2020-2026 unterschiedliche Werte) und kann
# ohne eine autoritative Quelle hier nicht verlässlich hinterlegt werden.
# Bis echte historische Jahreswerte nachgetragen sind, rechnet die Simulation
# mit einem konstanten Platzhalter-Basiszins für alle Jahre.
VORABPAUSCHALE_BASISZINS_PLATZHALTER = Decimal("0.02")  # 2,0% p.a., Platzhalter
VORABPAUSCHALE_FAKTOR = Decimal("0.70")  # gesetzlich fixierter Anteil des Basisertrags

# --- Spekulationsfrist für private Veräußerungsgeschäfte, z. B. BTC (#37) ---
#
# § 23 Abs. 3 Satz 5 EStG: Freigrenze (nicht Freibetrag!) für Gewinne aus
# privaten Veräußerungsgeschäften innerhalb der Spekulationsfrist - bleibt der
# Jahresgewinn darunter, bleibt er komplett steuerfrei; wird er überschritten,
# ist der GESAMTE Gewinn steuerpflichtig (kein Sockelbetrag wie beim
# Sparerpauschbetrag). Vereinfachung: Besteuerung mit dem pauschalen
# STEUERSATZ statt dem tatsächlich anzuwendenden persönlichen
# Einkommensteuersatz (siehe #37-Diskussion).
SPEKULATIONSFRIST_FREIGRENZE_PRO_JAHR = Decimal("1000")

# --- Dividendenrendite für ausschüttende Einzelaktien (#57) -----------------
#
# Bewusster Platzhalter nach demselben Muster wie
# VORABPAUSCHALE_BASISZINS_PLATZHALTER: statt für jede der 10 Einzelaktien-
# Satelliten eine echte historische Dividendenrendite zu recherchieren (manche
# zahlen wie Coca-Cola/Roche seit Jahrzehnten, andere wie Tesla/Palantir/
# Rivian/MSTR/BYD aktuell gar nichts), wird pauschal eine einheitliche
# Dividendenrendite angenommen - Owner-Entscheidung in #57. Wirkt in
# `engine.py` als jährlicher Bar-Ertrag (nicht nur Kostenbasis-Erhöhung wie
# bei der Vorabpauschale), der über den bestehenden Cash-Parken-Mechanismus
# automatisch reinvestiert wird und wie ein realer Kapitalertrag der
# Abgeltungsteuer unterliegt.
DIVIDENDENRENDITE_PLATZHALTER = Decimal("0.025")  # 2,5% p.a., Platzhalter
