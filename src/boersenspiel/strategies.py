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
)

STRATEGIES: list[Strategy] = [BARBELL_20_80, BARBELL_30_70, BARBELL_20_60_20_SATELLIT]

STRATEGIES_BY_NAME: dict[str, Strategy] = {s.name: s for s in STRATEGIES}


# --- Steuer- und Gebührenkonstanten (strategieübergreifend, aus dem Pflichtenheft) --

ORDERGEBUEHR = Decimal("1")  # Euro pro Trade (Kauf wie Verkauf)
STEUERSATZ = Decimal("0.26375")  # 25% Kapitalertragsteuer + 5,5% Soli darauf
SPARERPAUSCHBETRAG_PRO_JAHR = Decimal("1000")  # Euro, Reset zu Jahresbeginn
