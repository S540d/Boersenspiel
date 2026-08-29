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
    """Fünf strategieübergreifende Simulationsmechanismen, die ``engine.simulate()``
    unabhängig von der jeweiligen Gewichtungsregel anwendet. Einzeln ein-/ausschaltbar,
    damit ihr isolierter Renditebeitrag messbar wird (siehe #17), statt nur geglaubt zu
    werden. Defaults erhalten das bisherige Verhalten exakt - eine Strategie ohne
    explizit gesetztes ``optimierungen``-Feld verhält sich wie vor Einführung dieser
    Schalter."""

    steueroptimierung: bool = True  # Dezember-Harvest (Freibetrag-Gewinnmitnahme / Tax-Loss-Harvest)
    rebalancing: bool = True  # periodische Rückführung auf die Zielgewichte
    ordergebuehren: bool = True  # False -> gebührenfreie Referenzrechnung
    besteuerung: bool = True  # False -> realisierte Gewinne fließen nicht in Freibetrag/Steuer-Tracking
    fondskosten: bool = True  # False -> laufende Fondskosten (TER) bleiben unberücksichtigt (#76)


@dataclass(frozen=True)
class Strategy:
    name: str
    startkapital: Decimal
    toepfe: list[Topf]
    ziel_topf: str  # Name des Topfs, dessen Gewicht am Gesamtdepot überwacht wird (Rebalancing-Trigger)
    ziel_gewicht: Decimal  # Zielgewicht dieses Topfs, z. B. Decimal("0.20")
    rebalancing_schwelle_pp: Decimal  # Abweichung in Prozentpunkten, ab der rebalanciert wird
    # Relative Zusatzschwelle zur "5/25-Regel" (#63, F5): rebalanciert wird, sobald EIN
    # Topf entweder um mehr als rebalancing_schwelle_pp Prozentpunkte ABSOLUT vom
    # eigenen (umgelegten) Zielgewicht abweicht ODER um mehr als diesen Anteil RELATIV
    # zu seinem Zielgewicht (z. B. 0.25 = 25%, Marktstandard-"5/25-Regel") - je nachdem,
    # welche der beiden Schwellen zuerst greift. Default 1 (100% relativ) macht die
    # relative Schwelle faktisch wirkungslos (ein Zielgewicht kann nie um mehr als 100%
    # seiner selbst abweichen), sodass Strategien/Tests ohne explizit gesetzten Wert
    # sich weiterhin exakt wie vor #63 verhalten (nur die absolute Schwelle zählt).
    # Produktivstrategien setzen hier bewusst 0.25.
    rebalancing_schwelle_relativ: Decimal = Decimal("1")
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
    # Englische Fassung von ``beschreibung`` fürs clientseitige Sprachumschalten
    # im Dreipunktmenü. Leer bedeutet: keine Übersetzung hinterlegt (das Dashboard
    # zeigt dann weiterhin die deutsche Fassung, auch mit Englisch ausgewählt).
    beschreibung_en: str = ""
    # Welche der fünf Mechanismen aus ``Optimierungen`` für diese Strategie standardmäßig
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
    # Optional: True, wenn diese Strategie/dieses Szenario in ihrem Wertverlauf so
    # weit von den übrigen abweicht, dass eine gemeinsame Y-Achsen-Skalierung im
    # Dashboard (siehe dashboard._build_dashboard()) alle anderen Charts optisch
    # flach zeichnen würde - z. B. SP500_BENCHMARK, dessen Endwert ein Vielfaches
    # der übrigen Strategien beträgt. Rein darstellerisch (Startseite), ändert
    # nichts an der Simulation. Default False = fließt normal ins gemeinsame
    # Chart-Maximum ein.
    eigene_chart_skala: bool = False
    # Optional: False nimmt diese Strategie/dieses Szenario aus dem
    # CAGR-Balkendiagramm der Startseite heraus (#92). Rein darstellerisch: die
    # Zeile bleibt in der Vergleichstabelle, bekommt weiter ihre Detailseite und
    # ihren Wertverlauf-Chart, taucht nur in dem einen Übersichts-Balkendiagramm
    # nicht mehr auf. Hintergrund: mit allen 16 Läufen war das Diagramm so dicht,
    # dass Chart.js nur noch jede zweite Achsenbeschriftung zeichnete - es
    # standen also mehr Balken da als Namen. Owner-Auswahl zu #92; die
    # Börsenweisheiten haben mit "<Kombi-Name> im Vergleich" ohnehin einen
    # eigenen Chart auf derselben Seite.
    im_startseiten_chart: bool = True
    # Optional: Name der Rubrik, unter der diese Strategie/dieses Szenario auf der
    # Startseite gruppiert wird (#94) - z. B. "Barbell-Varianten", "Charttechnik".
    # None bedeutet: kein Rubrik-Chart, die Strategie erscheint nur im
    # CAGR-Balkendiagramm und der Vergleichstabelle, nicht in einer der
    # gruppierten Übersichten (aktuell nur für Sonderfälle gedacht, alle
    # produktiven Strategien/Szenarien setzen eine Rubrik). Rein deklarativ,
    # ändert nichts an der Simulation - siehe ``dashboard._rubrik_gruppen()``.
    rubrik: str | None = None

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


# --- Rubriken (#94) --------------------------------------------------------
#
# Vier Kategorien, unter denen die Startseite Strategien/Szenarien gruppiert
# (siehe dashboard._rubrik_gruppen()): je Rubrik ein gemeinsamer
# Vergleichschart aller Mitglieder plus eine Aufzählung ihrer
# Kurzbeschreibungen statt eines einzelnen Charts je Strategie. Der
# S&P-500-Benchmark bekommt bewusst eine eigene, fünfte Rubrik statt der
# Barbell-Rubrik - er ist strukturell kein Barbell, sondern eine reine
# Vergleichslinie "einfach den Index kaufen" (Owner-Entscheidung zu #94).
RUBRIK_BARBELL = "Barbell-Varianten"
RUBRIK_BOERSENWEISHEITEN = "Börsenweisheiten"
RUBRIK_CHARTTECHNIK = "Charttechnik"
RUBRIK_WEITERE_ANALYSEN = "Weitere Analysen"
RUBRIK_REFERENZ = "Referenz"
RUBRIK_KLASSISCHE_PORTFOLIOS = "Klassische Portfolios"
RUBRIK_FAKTOR = "Faktor-Strategien"

# --- Strategie 1: Barbell 20/80 (Pflichtenheft v2.0) -----------------------

BARBELL_20_80 = Strategy(
    name="Barbell 20/80",
    rubrik=RUBRIK_BARBELL,
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
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "20% Sicherheit (breite Anleihen/Gold-ETFs), 80% Wachstum (breite Aktien-ETFs "
        "plus Bitcoin). Rebalancing auf die Zielgewichte, sobald EIN Topf um mehr als "
        "5 Prozentpunkte absolut oder 25% relativ vom eigenen Zielgewicht abweicht "
        "(5/25-Regel, marktüblich)."
    ),
    beschreibung_en=(
        "20% safety (broad bond/gold ETFs), 80% growth (broad equity ETFs plus "
        "Bitcoin). Rebalances to the target weights as soon as ONE bucket deviates "
        "by more than 5 percentage points absolute or 25% relative to its own target "
        "weight (5/25 rule, market-standard)."
    ),
)

# --- Strategie 2: Barbell 30/70 (Beispiel für eine alternative Gewichtung) -

BARBELL_30_70 = Strategy(
    name="Barbell 30/70",
    rubrik=RUBRIK_BARBELL,
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
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Defensivere Variante des Barbell-Ansatzes: 30% Sicherheit statt 20%, dafür "
        "70% Wachstum. Rebalancing-Trigger wie bei Barbell 20/80 (5/25-Regel je Topf)."
    ),
    beschreibung_en=(
        "A more defensive variant of the barbell approach: 30% safety instead of "
        "20%, and 70% growth. Rebalancing trigger the same as Barbell 20/80 "
        "(5/25 rule per bucket)."
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
    rubrik=RUBRIK_BARBELL,
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
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Wie Barbell 20/80, aber der Wachstums-Topf sinkt von 80% auf 60% zugunsten "
        "eines dritten, gleichgewichteten Topfs aus 10 Einzelaktien (20%) - das "
        "80/20-Risikoprofil bleibt erhalten, nur granularer gestreut. Die "
        "Aktienauswahl selbst ist eine heutige (2026er) Zusammenstellung, kein "
        "1:1-Nachbau einer 2021 tatsaechlich getroffenen Entscheidung - siehe die "
        "Variante mit defensiverem Tilt daneben fuer eine Gegenprobe, wie stark "
        "das Ergebnis von zwei rueckblickend dominanten Einzelwerten abhaengt."
    ),
    beschreibung_en=(
        "Like Barbell 20/80, but the growth bucket shrinks from 80% to 60% in "
        "favor of a third, equally weighted bucket of 10 individual stocks (20%) - "
        "the 80/20 risk profile stays the same, just spread more granularly. The "
        "stock selection itself reflects today's (2026) vantage point, not a "
        "faithful reconstruction of a decision actually made in 2021 - see the "
        "defensive-tilt variant alongside it for a check on how much the result "
        "depends on two names that only look obviously dominant in hindsight."
    ),
)

# --- Strategie 3b: Einzelaktien-Satellit mit defensiverem Tilt -------------
#
# Reaktion auf den in einer Projektpruefung festgestellten Rueckschaufehler bei
# der Aktienauswahl: die zehn Einzelaktien in BARBELL_20_60_20_SATELLIT sind zu
# gleichen Teilen (je 10%) gewichtet, darunter LITE (Lumentum) und PLTR
# (Palantir) - beide legten ueber den Vergleichszeitraum ganz ueberwiegend seit
# dem KI-Boom ab 2023 um mehrere hundert Prozent zu (+897%/+703%), eine
# Groessenordnung, die 2021 kein plausibler Anlagethesen-Bestandteil war,
# sondern erst im Rueckblick so aussieht. Nimmt man beide aus dem Topf heraus
# und verteilt ihr Gewicht gleichmaessig auf die uebrigen acht, faellt die
# Strategie ueber denselben Zeitraum von +145,8% auf +97,3% CAGR-Basis und
# damit UNTER das einfache Barbell 20/80 (+118,9%) - der Renditevorsprung des
# Satelliten-Topfs steht und faellt praktisch komplett mit diesen zwei Werten.
#
# Diese Variante ersetzt NICHT die Instrumente (keine neuen Ticker, kein
# zusaetzlicher Alpha-Vantage-Request - das Tagesbudget ist mit 24 Instrumenten
# bereits voll ausgeschoepft, siehe instruments.py), sondern gewichtet um:
# LITE und PLTR sinken von je 10% auf je 5%, die freiwerdenden 10 Prozentpunkte
# wandern zu gleichen Teilen auf die beiden bereits vorhandenen defensiven Blue
# Chips Coca-Cola und Roche (von je 10% auf je 15%) - die beiden "langlaufenden,
# stabilen Werte", die eine 2021 zusammengestellte Auswahl eher getragen haette
# als eine Wette auf eine damals noch nicht abzusehende KI-Rally. Die uebrigen
# sechs Positionen (BYDDY, SEDG, S92, TSLA, MSTR, RIVN) bleiben bei je 10% -
# das waren 2021 bereits grosse, oeffentlich bekannte Werte (Tesla als
# wertvollster Autohersteller, BYD als Chinas fuehrender NEV-Hersteller,
# MicroStrategy/Strategy fuer seine seit 2020 bekannte Bitcoin-Treasury-
# Strategie), keine erst im Rueckblick auffaelligen Ausreisser wie LITE/PLTR.
#
# Ausdruecklich KEIN Anspruch, tatsaechlich zu rekonstruieren, was 2021 gewaehlt
# worden waere (das laesst sich nicht nachpruefen) - wie jedes Szenario in
# diesem Projekt ein erster, nicht optimierter/gebacktesteter Ansatz, hier
# gezielt als Gegenprobe zur rueckschaufehler-anfaelligen Originalauswahl.

BARBELL_20_60_20_SATELLIT_DEFENSIV = Strategy(
    name="Barbell 20/60/20 + Einzelaktien-Satellit (defensiver Tilt)",
    rubrik=RUBRIK_BARBELL,
    im_startseiten_chart=False,
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
            name="Topf C - Einzelaktien-Satellit (defensiver Tilt)",
            gewicht_gesamt=Decimal("0.20"),
            sub_gewichte={
                "LITE": Decimal("0.05"),
                "BYDDY": Decimal("0.10"),
                "SEDG": Decimal("0.10"),
                "S92": Decimal("0.10"),
                "TSLA": Decimal("0.10"),
                "PLTR": Decimal("0.05"),
                "MSTR": Decimal("0.10"),
                "RIVN": Decimal("0.10"),
                "KO": Decimal("0.15"),
                "RHHBY": Decimal("0.15"),
            },
        ),
    ],
    ziel_topf="Topf A - Sicherheit",
    ziel_gewicht=Decimal("0.20"),
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Wie Barbell 20/60/20 + Einzelaktien-Satellit, aber mit einem defensiveren "
        "Tilt innerhalb des Aktien-Topfs: LITE und PLTR sinken von je 10% auf je "
        "5%, die freiwerdenden 10 Prozentpunkte gehen zu gleichen Teilen an die "
        "beiden defensiven Blue Chips Coca-Cola und Roche (je 15% statt 10%). "
        "Hintergrund: LITE (+897%) und PLTR (+703%) tragen im Vergleichszeitraum "
        "praktisch den gesamten Renditevorsprung der Originalauswahl gegenueber "
        "Barbell 20/80 - eine Groessenordnung, die erst seit dem KI-Boom ab 2023 "
        "sichtbar wurde und 2021 kein plausibler Bestandteil einer Anlagethese "
        "gewesen waere. Kein Rekonstruktionsversuch einer tatsaechlich 2021 "
        "getroffenen Wahl, sondern eine Gegenprobe, wie stark das Ergebnis der "
        "Originalauswahl von diesen zwei Werten abhaengt."
    ),
    beschreibung_en=(
        "Like the Barbell 20/60/20 + individual-stock satellite, but with a more "
        "defensive tilt within the stock bucket: LITE and PLTR drop from 10% each "
        "to 5% each, and the freed 10 percentage points go equally to the two "
        "defensive blue chips Coca-Cola and Roche (15% each instead of 10%). "
        "Rationale: LITE (+897%) and PLTR (+703%) account for practically all of "
        "the original selection's outperformance over Barbell 20/80 in the "
        "comparison window - a magnitude that only became visible from the 2023 "
        "AI boom onward and would not have been a plausible investment thesis in "
        "2021. Not an attempt to reconstruct what was actually chosen in 2021, "
        "but a check on how much the original selection's result depends on "
        "these two names."
    ),
)

# --- Strategie 4: Barbell 20/80, breiter diversifiziert (#64) --------------
#
# Adressiert zwei im Rahmen von #64 identifizierte Luecken der bestehenden
# Barbell-Strategien: Topf A ("Sicherheit") besteht bei BARBELL_20_80 zur
# Haelfte aus EUNL (Aktien-ETF) - kein echter Cash-Baustein. Und Topf B
# ("Wachstum") ist stark auf USA/Tech konzentriert (LYMS Nasdaq-100 + SEMI
# Halbleiter = 70% des Topfs), Europa fehlt komplett, ebenso Immobilien und
# breite Rohstoffe. Diese Variante nutzt sechs der sieben in #64 ergaenzten
# Instrumente (alle ausser IUSA, das ausschliesslich als Benchmark dient,
# siehe SP500_BENCHMARK unten), um beide Luecken zu schliessen, OHNE
# BARBELL_20_80 selbst zu veraendern - eine zusaetzliche Strategie neben den
# bestehenden, nach demselben Muster wie BARBELL_20_60_20_SATELLIT.
#
# Topf A behaelt 20% des Gesamtdepots, verliert aber EUNL zugunsten von
# echtem EUR-Cash (XEON) sowie Anleihen mit anderer Duration/Realzins-
# Eigenschaft (IBCL/IBCI) neben EUNA/4GLD. Topf B behaelt 80%, EUNL wandert
# hierher, EXSA (Europa) senkt den USA/Tech-Anteil, IQQ6/EXXY ergaenzen
# Immobilien und breite Rohstoffe. Erster Ansatz, Gewichte nicht optimiert
# oder gebacktestet (wie alle Szenarien in scenarios.py).

BARBELL_20_80_DIVERSIFIZIERT = Strategy(
    name="Barbell 20/80 (breiter diversifiziert)",
    rubrik=RUBRIK_BARBELL,
    im_startseiten_chart=False,
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Sicherheit",
            gewicht_gesamt=Decimal("0.20"),
            sub_gewichte={
                "EUNA": Decimal("0.25"),
                "4GLD": Decimal("0.15"),
                "XEON": Decimal("0.25"),
                "IBCL": Decimal("0.15"),
                "IBCI": Decimal("0.20"),
            },
        ),
        Topf(
            name="Topf B - Wachstum",
            gewicht_gesamt=Decimal("0.80"),
            sub_gewichte={
                "EUNL": Decimal("0.25"),
                "EXSA": Decimal("0.15"),
                "LYMS": Decimal("0.20"),
                "SEMI": Decimal("0.10"),
                "EIMI": Decimal("0.15"),
                "IQQ6": Decimal("0.05"),
                "EXXY": Decimal("0.05"),
                "BTC-EUR": Decimal("0.05"),
            },
        ),
    ],
    ziel_topf="Topf A - Sicherheit",
    ziel_gewicht=Decimal("0.20"),
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Wie Barbell 20/80 (20% Sicherheit / 80% Wachstum), aber mit breiterer "
        "Streuung der sechs in #64 ergänzten Instrumente: Topf A bekommt mit XEON "
        "(EUR-Geldmarkt) einen echten Cash-Baustein statt eines Aktien-ETF-Anteils, "
        "dazu IBCL/IBCI (Anleihen anderer Duration/Realzins-Eigenschaft). Topf B "
        "reduziert die USA/Tech-Konzentration durch EXSA (Europa) und ergänzt "
        "Immobilien (IQQ6) und breite Rohstoffe (EXXY). Erster Ansatz, nicht "
        "optimiert/gebacktestet."
    ),
    beschreibung_en=(
        "Like Barbell 20/80 (20% safety / 80% growth), but with broader "
        "diversification across the six instruments added in #64: bucket A gets a "
        "real cash building block via XEON (EUR money market) instead of an equity "
        "ETF share, plus IBCL/IBCI (bonds with a different duration/real-rate "
        "profile). Bucket B reduces the US/tech concentration via EXSA (Europe) and "
        "adds real estate (IQQ6) and broad commodities (EXXY). A first approach, "
        "not optimized or backtested."
    ),
)

# --- Strategie 5: Benchmark S&P 500 (#64) -----------------------------------
#
# Reine Vergleichslinie: "einfach den Index kaufen", das Dashboard hatte
# bislang keine solche Referenz gegen die aktiven Strategien/Szenarien. Ein
# einziger Topf mit 100% IUSA, nie rebalanciert - bei nur einem Instrument
# waere ein Rebalancing-Trigger ohnehin wirkungslos, aber wie bei
# scenarios.BUY_AND_HOLD wird das bewusst ehrlich ueber den
# Optimierungs-Schalter (#17) abgeschaltet statt implizit ueber eine
# unerreichbare Schwelle.

SP500_BENCHMARK = Strategy(
    name="Benchmark: S&P 500 (Buy & Hold)",
    rubrik=RUBRIK_REFERENZ,
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Benchmark",
            gewicht_gesamt=Decimal("1"),
            sub_gewichte={"IUSA": Decimal("1")},
        ),
    ],
    ziel_topf="Topf A - Benchmark",
    ziel_gewicht=Decimal("1"),
    rebalancing_schwelle_pp=Decimal("5"),
    optimierungen=Optimierungen(rebalancing=False),
    beschreibung=(
        "Reine Vergleichslinie: einmaliger Kauf von IUSA (S&P 500, USD, EUR-notiert "
        "an der Xetra), nie aktiv umgeschichtet. Dient als Referenz 'einfach den "
        "Index kaufen' gegenüber den Barbell-Strategien und Szenarien, kein "
        "eigenständiger Anlagevorschlag."
    ),
    beschreibung_en=(
        "A pure comparison line: a single purchase of IUSA (S&P 500, USD, "
        "EUR-quoted on Xetra), never actively reshuffled. Serves as the 'simply "
        "buy the index' reference against the barbell strategies and scenarios, "
        "not a standalone investment recommendation."
    ),
    # Waechst ueber 20 Jahre auf ein Vielfaches der uebrigen Strategien - mit
    # gemeinsamer Y-Achse wuerden alle anderen Wertverlauf-Charts auf der
    # Startseite optisch flach.
    eigene_chart_skala=True,
)

# --- Strategie 6: Klassisches 60/40-Portfolio -------------------------------
#
# Das meistzitierte Referenzportfolio überhaupt: 60% breiter Aktienmarkt,
# 40% Anleihen. Anders als die Barbell-Strategien kein Extrem-Ansatz
# (sicherer Sockel + volatile Beimischung), sondern eine einzige, glatte
# Aufteilung zwischen den beiden Anlageklassen - die naheliegendste
# Vergleichslinie neben "einfach den Index kaufen" (SP500_BENCHMARK). Nutzt
# ausschließlich bereits vorhandene Instrumente (EUNL/EUNA), kein
# zusätzlicher API-Request.

PORTFOLIO_60_40 = Strategy(
    name="60/40-Portfolio",
    rubrik=RUBRIK_KLASSISCHE_PORTFOLIOS,
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Aktien",
            gewicht_gesamt=Decimal("0.60"),
            sub_gewichte={"EUNL": Decimal("1")},
        ),
        Topf(
            name="Topf B - Anleihen",
            gewicht_gesamt=Decimal("0.40"),
            sub_gewichte={"EUNA": Decimal("1")},
        ),
    ],
    ziel_topf="Topf A - Aktien",
    ziel_gewicht=Decimal("0.60"),
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Das klassische 60/40-Depot: 60% breiter Aktienmarkt (MSCI World), 40% "
        "breite Anleihen. Kein Extrem-Ansatz wie die Barbell-Strategien, sondern "
        "eine einzige glatte Aufteilung zwischen den beiden Anlageklassen. "
        "Rebalancing nach der 5/25-Regel wie bei den übrigen Strategien."
    ),
    beschreibung_en=(
        "The classic 60/40 portfolio: 60% broad equities (MSCI World), 40% broad "
        "bonds. Unlike the barbell strategies, not an extreme-allocation approach "
        "but a single smooth split between the two asset classes. Rebalances "
        "using the same 5/25 rule as the other strategies."
    ),
)

# --- Strategie 7: Permanent Portfolio (Harry Browne) ------------------------
#
# Vier gleich große Töpfe (je 25%) über Aktien, lange Anleihen, Gold und
# Cash - konzipiert, um in jedem der vier Wirtschaftsklimata (Wachstum,
# Rezession, Inflation, Deflation) mindestens einen gut laufenden Baustein zu
# halten. Inhaltlich der interessanteste Kontrast zur Barbell-Idee: statt
# zwei Extremen (sicher/riskant) vier gleichgewichtete, unterschiedlich auf
# Marktphasen reagierende Bausteine. Nutzt ausschließlich bereits vorhandene
# Instrumente (EUNL/IBCL/4GLD/XEON), kein zusätzlicher API-Request.

PERMANENT_PORTFOLIO = Strategy(
    name="Permanent Portfolio",
    rubrik=RUBRIK_KLASSISCHE_PORTFOLIOS,
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Aktien",
            gewicht_gesamt=Decimal("0.25"),
            sub_gewichte={"EUNL": Decimal("1")},
        ),
        Topf(
            name="Topf B - Lange Anleihen",
            gewicht_gesamt=Decimal("0.25"),
            sub_gewichte={"IBCL": Decimal("1")},
        ),
        Topf(
            name="Topf C - Gold",
            gewicht_gesamt=Decimal("0.25"),
            sub_gewichte={"4GLD": Decimal("1")},
        ),
        Topf(
            name="Topf D - Cash",
            gewicht_gesamt=Decimal("0.25"),
            sub_gewichte={"XEON": Decimal("1")},
        ),
    ],
    ziel_topf="Topf A - Aktien",
    ziel_gewicht=Decimal("0.25"),
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Harry Brownes Permanent Portfolio: je 25% Aktien (MSCI World), lange "
        "Staatsanleihen, Gold und Cash - ein Baustein je Wirtschaftsklima "
        "(Wachstum, Rezession, Inflation, Deflation). Vier gleichgewichtete Töpfe "
        "statt zweier Extreme wie bei den Barbell-Strategien. Rebalancing nach "
        "der 5/25-Regel."
    ),
    beschreibung_en=(
        "Harry Browne's Permanent Portfolio: 25% each in equities (MSCI World), "
        "long-term government bonds, gold and cash - one building block for each "
        "economic climate (growth, recession, inflation, deflation). Four "
        "equally weighted buckets instead of the two extremes used by the "
        "barbell strategies. Rebalances using the 5/25 rule."
    ),
)

# --- Strategie 8: Dividende & Value (#99) -----------------------------------
#
# Dividenden-Investing und Value/Faktor-Investing gehoeren zu den bekanntesten
# Boersenstrategien ueberhaupt - im bisherigen Instrumentenset hatte aber kein
# einziges Instrument eine gezielte Dividenden- oder Value-Ausrichtung.
# `Instrument.dividendenrendite` (#74) modelliert nur die Ausschuettung der
# ohnehin gehaltenen Instrumente; das ist etwas anderes als ein Portfolio, das
# nach genau diesem Merkmal AUSGEWAEHLT wird.
#
# Anders als das 60/40- und das Permanent Portfolio (#98) liess sich das nicht
# aus vorhandenen Instrumenten bauen: ISPA und IS3S sind dafuer neu
# dazugekommen (siehe instruments.py) - und mit ihnen die Aufteilung des
# Wochenabrufs auf zwei Tage, weil das Alpha-Vantage-Tagesbudget vorher exakt
# ausgeschoepft war (siehe sources/alphavantage.py, `batch_tickers`).
#
# Umgesetzt ist die im Issue vorgeschlagene KOMBINIERTE Variante (ein
# Strategie-Eintrag, zwei gleich grosse Toepfe) statt zweier getrennter
# Strategien - die Vergleichstabelle bleibt damit kompakt. Der isolierte
# Effekt jedes der beiden Faktoren bleibt trotzdem sichtbar: die Detailseite
# weist die Topf-Gewichtung Ist/Ziel je Topf aus. Sollen "Dividende" und
# "Value" spaeter als eigene Zeilen verglichen werden, sind das zwei weitere
# Eintraege in STRATEGIES ohne jede Aenderung an Engine oder Dashboard.
#
# Eigene Rubrik statt RUBRIK_KLASSISCHE_PORTFOLIOS: 60/40 und Permanent
# Portfolio teilen ueber ANLAGEKLASSEN auf (Aktien/Anleihen/Gold/Cash), diese
# Strategie dagegen innerhalb derselben Anlageklasse nach einem
# AUSWAHLMERKMAL der Aktien. Das ist der inhaltlich andere Ansatz, und die
# Rubrik ist der Platz fuer weitere Faktoren (Quality, Low-Vol, Momentum als
# eigenes Instrument), die dieses Issue bewusst ausklammert.
#
# Erster Ansatz wie bei allen Strategien: die 50/50-Aufteilung ist die
# naheliegende Gleichgewichtung, nicht fuer dieses Instrumentenset optimiert
# oder gebacktestet. IS3S hat erst ab November 2014 Kurse - die Strategie
# startet nach der F4-Regel (#63) entsprechend spaeter als die uebrigen.

DIVIDENDE_UND_VALUE = Strategy(
    name="Dividende & Value",
    rubrik=RUBRIK_FAKTOR,
    startkapital=Decimal("10000"),
    toepfe=[
        Topf(
            name="Topf A - Dividende",
            gewicht_gesamt=Decimal("0.50"),
            sub_gewichte={"ISPA": Decimal("1")},
        ),
        Topf(
            name="Topf B - Value",
            gewicht_gesamt=Decimal("0.50"),
            sub_gewichte={"IS3S": Decimal("1")},
        ),
    ],
    ziel_topf="Topf A - Dividende",
    ziel_gewicht=Decimal("0.50"),
    rebalancing_schwelle_pp=Decimal("5"),
    rebalancing_schwelle_relativ=Decimal("0.25"),
    beschreibung=(
        "Zwei der meistdiskutierten Aktien-Stile zu gleichen Teilen: 50% "
        "Dividendenstrategie (STOXX Global Select Dividend 100 - die 100 "
        "dividendenstärksten Titel weltweit) und 50% Value-Faktor (MSCI World "
        "Value - günstig bewertete Titel des Weltindex). Anders als bei 60/40 "
        "oder dem Permanent Portfolio wird hier nicht zwischen Anlageklassen "
        "aufgeteilt, sondern innerhalb der Aktien nach einem Auswahlmerkmal. "
        "Rebalancing nach der 5/25-Regel."
    ),
    beschreibung_en=(
        "Two of the most widely discussed equity styles in equal parts: 50% "
        "dividend strategy (STOXX Global Select Dividend 100 - the 100 highest "
        "dividend-paying stocks worldwide) and 50% value factor (MSCI World "
        "Value - the cheaply valued names in the world index). Unlike 60/40 or "
        "the Permanent Portfolio, this splits within equities by a selection "
        "criterion rather than across asset classes. Rebalances using the 5/25 "
        "rule."
    ),
)

STRATEGIES: list[Strategy] = [
    BARBELL_20_80,
    BARBELL_30_70,
    BARBELL_20_60_20_SATELLIT,
    BARBELL_20_60_20_SATELLIT_DEFENSIV,
    BARBELL_20_80_DIVERSIFIZIERT,
    SP500_BENCHMARK,
    PORTFOLIO_60_40,
    PERMANENT_PORTFOLIO,
    DIVIDENDE_UND_VALUE,
]

STRATEGIES_BY_NAME: dict[str, Strategy] = {s.name: s for s in STRATEGIES}

# --- Benchmarks für den Diagramm-Overlay-Schalter (#72) ----------------------
#
# Strategien hier stehen bewusst NICHT in STRATEGIES/SCENARIOS - sie sollen
# nicht als eigene Zeile in der Vergleichsübersicht erscheinen, sondern nur
# optional als Vergleichslinie in den Wertverlauf-Charts ANDERER Strategien
# eingeblendet werden (der zentrale Schalter aus #72). `dashboard.py`
# simuliert jede hier gelistete Strategie mit dem Startkapital der jeweils
# angezeigten Strategie neu (`dataclasses.replace`), damit die Overlay-Linie
# bei demselben Startwert beginnt wie die Strategie selbst - deshalb muss
# jeder Eintrag ein reiner Einzelinstrument-Buy&Hold sein (ein Topf, 100%,
# `optimierungen=Optimierungen(rebalancing=False)`). Ein Kandidat wird dem
# Dashboard nur angeboten, wenn sein Ticker im jeweils angezeigten Zeitraum
# tatsächlich einen Kurs hat (siehe `dashboard._benchmark_reihen()`) - aktuell
# also nur `SP500_BENCHMARK`. `FR0010755611` (Amundi MSCI USA Daily (2x)
# Leveraged, aus #72) ist bewusst noch NICHT ergänzt: das wäre ein 25.
# Instrument und würde das Alpha-Vantage-Tagesbudget von 25 auf 26 Requests
# reißen (siehe instruments.py "Request-Budget", #64). Der Schalter selbst
# ist generisch für weitere Kandidaten gebaut - sobald das Instrument mit
# eigenem Budget-Spielraum (oder Ersatz eines bestehenden) ergänzt wird,
# reicht ein weiterer Eintrag in dieser Liste.
BENCHMARK_STRATEGIEN: list[Strategy] = [SP500_BENCHMARK]


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
