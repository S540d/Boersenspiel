"""Rendert Simulationsergebnisse als statisches HTML-Dashboard (Chart.js, CDN).

Läuft standardmäßig gegen ALLE in ``strategies.py`` hinterlegten Strategien
und zeigt sie nebeneinander, damit unterschiedliche Strategien direkt
verglichen werden können. Reine Darstellungsschicht ohne eigene
Berechnungslogik - alle Zahlen kommen aus ``engine.simulate()``.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import SimulationResult, simulate
from .history_store import FetchLogEntry, PriceRow
from .instruments import INSTRUMENTS, TICKERS, Instrument
from .learnings import derive_learnings
from .strategies import (
    BENCHMARK_STRATEGIEN,
    DIVIDENDENRENDITE_PLATZHALTER,
    ORDERGEBUEHR,
    SPARERPAUSCHBETRAG_PRO_JAHR,
    SPEKULATIONSFRIST_FREIGRENZE_PRO_JAHR,
    STEUERSATZ,
    STRATEGIES,
    VORABPAUSCHALE_BASISZINS_PLATZHALTER,
    VORABPAUSCHALE_FAKTOR,
    Strategy,
    Topf,
)

# Status-Werte in fetch_log.csv, bei denen der Kurs NICHT frisch abgerufen wurde
# (siehe history_store.record_week) - Grundlage fuer die "eingefroren"-Markierung (#42).
_STALE_STATUS = {"carried_forward", "missing"}

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "index.html"

# Anzeigenamen der fuenf Optimierungs-Schalter (siehe strategies.Optimierungen / #17).
_OPTIMIERUNGS_LABELS: dict[str, str] = {
    "steueroptimierung": "Steueroptimierung (Dezember-Harvest)",
    "rebalancing": "Rebalancing",
    "ordergebuehren": "Ordergebühren",
    "besteuerung": "Besteuerung",
    "fondskosten": "Laufende Fondskosten (TER)",
}
# Englische Fassung fuers clientseitige Sprachumschalten (Dreipunktmenue).
_OPTIMIERUNGS_LABELS_EN: dict[str, str] = {
    "steueroptimierung": "Tax optimization (December harvest)",
    "rebalancing": "Rebalancing",
    "ordergebuehren": "Order fees",
    "besteuerung": "Taxation",
    "fondskosten": "Ongoing fund costs (TER)",
}


# --- F4 (#63): Rueckschaufehler mit Hebel ------------------------------------------
#
# Ein 2026 zusammengestelltes Instrumentenset rueckwirkend ab 2006 zu kaufen ist
# Look-ahead-Bias, verschaerft durch handelbare_gewichte() (#55): solange nur ein
# Teil der Zielinstrumente existiert, wird deren Gewicht anteilig auf die
# VORHANDENEN umgelegt - trifft das ein extrem volatiles, frueh existierendes
# Instrument wie Bitcoin, wird dessen effektive Zielquote fuer Jahre auf fast das
# Doppelte hochskaliert. Owner-Entscheidung zu #63 (F4): zwei Massnahmen, beide rein
# in der Darstellungsschicht (dashboard.py) angewandt, bevor rows an simulate()
# gehen - engine.py bleibt unveraendert, die vorhandenen Engine-Tests (hand-
# gerechnete Werte gegen die volle Testhistorie) sind davon nicht betroffen.
_BTC_TICKER = "BTC-EUR"
# Bitcoin ohne EUR-Handelsplatz mit Zugang fuer deutsche Privatanleger: Kraken
# eroeffnete EUR-Paare erst 09/2013, echte Mainstream-Broker/Boersen-Anbindung
# (Coinbase-EU-Rollout u.ae.) erst ab ca. 2017 - vor diesem Datum war Bitcoin fuer
# einen deutschen Privatanleger ueber einen Broker faktisch nicht erreichbar
# (Owner-Entscheidung). Bewusster Platzhalter nach demselben Muster wie
# VORABPAUSCHALE_BASISZINS_PLATZHALTER, kein historisch belegtes Stichdatum.
_BTC_FRUEHPHASE_ENDE = date(2017, 1, 1)


def _ohne_btc_fruehphase(rows: list[PriceRow]) -> list[PriceRow]:
    """Entfernt BTC-EUR-Kurse vor ``_BTC_FRUEHPHASE_ENDE`` aus den Zeilen - Bitcoin
    gilt fuer die Simulation bis dahin als nicht handelbar, genau wie ein
    Instrument, das seinen Boersengang noch nicht hatte. Nutzt damit denselben,
    bereits vorhandenen Mechanismus (handelbare_gewichte() in engine.py) fuer
    "noch nicht existierende" Instrumente, statt eine neue Sonderregel in die
    Engine einzubauen."""
    bereinigt = []
    for row in rows:
        if row.date < _BTC_FRUEHPHASE_ENDE and _BTC_TICKER in row.prices:
            neue_prices = {t: p for t, p in row.prices.items() if t != _BTC_TICKER}
            row = replace(row, prices=neue_prices)
        bereinigt.append(row)
    return bereinigt


def _real_investierbarer_zeitraum(rows: list[PriceRow], strategy: Strategy) -> list[PriceRow]:
    """Schneidet die Kurshistorie auf den Zeitraum zurecht, ab dem ALLE
    Zielinstrumente dieser Strategie tatsaechlich einen Kurs haben - vorher
    verlaesst sich handelbare_gewichte() auf die anteilige Umlegung (#55), was fuer
    die veroeffentlichten Kennzahlen genau die Verzerrung erzeugt, die F4
    beschreibt. Je Strategie unterschiedlich, weil unterschiedliche Strategien
    unterschiedliche Instrumentensets haben. Betrifft nur die fuer die
    Darstellung verwendeten rows, nicht die Rohdaten in price_history.csv."""
    ziel_ticker = set(strategy.alle_ticker_gewichte())
    for i, row in enumerate(rows):
        if ziel_ticker <= set(row.prices):
            return rows[i:]
    return rows


# --- #80: laengerer Betrachtungszeitraum per Ersatzbond-Annahme --------------
#
# _real_investierbarer_zeitraum() (F4/#63) schneidet die Historie auf den
# Zeitpunkt zurecht, ab dem ALLE Zielinstrumente einer Strategie handelbar
# sind - das vermeidet die Ueberkonzentration frueh existierender Instrumente
# (v.a. Bitcoin), kostet aber Jahre an Historie: die dreitoepfige
# Barbell-Strategie startet dadurch z.B. erst 2021 statt am Beginn der
# Kurshistorie (2006). Issue #80 bittet ausdruecklich um einen laengeren
# Zeitraum, mit der Annahme, dass das Kapital eines noch nicht handelbaren
# Zielinstruments bis zu dessen Verfuegbarkeit in einem einzigen, fuer ALLE
# Strategien/Szenarien GLEICHEN Anleihe-ETF angelegt war - "nicht
# unterschiedliche Bonds" war eine explizite Vorgabe im Issue.
#
# IBCL (Euro-Staatsanleihen 15-30 Jahre) ist die Wahl: eine echte Anleihe (im
# Gegensatz zum Geldmarkt-ETF XEON, der als Cash-Aequivalent fuer den
# risikofreien Zins dient, siehe _GELDMARKT_TICKER) mit der laengsten
# verfuegbaren Historie unter den Anleihen-ETFs (ab 2007-05-18, siehe
# instruments.py) - deckt damit fast die gesamte Kurshistorie ab 2006-09 ab.
#
# Umsetzung ueber Strategy.gewichte_fn statt eines Engine-Eingriffs (dieselbe
# Erweiterungsstelle wie scenarios.py): die Ziel-Gewichte jedes noch nicht
# handelbaren Instruments wandern in dieser Zeile auf IBCL um, statt wie in
# handelbare_gewichte() anteilig auf die UEBRIGEN Zielinstrumente verteilt zu
# werden (genau das war die in F4 beschriebene Ueberkonzentration). IBCL muss
# dafuer Teil von Strategy.alle_ticker_gewichte() sein, sonst wuerde
# engine.simulate() das umgeleitete Gewicht stillschweigend verwerfen (siehe
# engine.rebalance_to_targets(): iteriert nur ueber die beim Start fixierten
# `tickers`) - ist IBCL noch nicht Teil der Strategie, ergaenzt ein
# zusaetzlicher Topf mit Gesamtgewicht 0 es, ohne die eigentlichen
# Topf-Zielgewichte zu veraendern.
_ERSATZBOND_TICKER = "IBCL"


def _mit_ersatzbond(strategy: Strategy) -> Strategy:
    """Erweitert ``strategy`` um die Ersatzbond-Annahme aus #80 (siehe oben)."""
    basis_gewichte = strategy.alle_ticker_gewichte()
    hat_ersatzbond_schon = _ERSATZBOND_TICKER in basis_gewichte
    toepfe = strategy.toepfe
    if not hat_ersatzbond_schon:
        toepfe = [
            *toepfe,
            Topf(
                name="Ersatzbond (vor Verfügbarkeit, #80)",
                gewicht_gesamt=Decimal(0),
                sub_gewichte={_ERSATZBOND_TICKER: Decimal(1)},
            ),
        ]
    urspruengliche_gewichte_fn = strategy.gewichte_fn

    def gewichte_fn(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
        gewichte = dict(
            urspruengliche_gewichte_fn(rows, i) if urspruengliche_gewichte_fn is not None else basis_gewichte
        )
        prices = rows[i].prices
        ersatz_anteil = Decimal(0)
        for ticker in list(gewichte):
            if ticker == _ERSATZBOND_TICKER:
                continue
            gewicht = gewichte[ticker]
            if gewicht > 0 and ticker not in prices:
                ersatz_anteil += gewicht
                gewichte[ticker] = Decimal(0)
        if ersatz_anteil > 0:
            gewichte[_ERSATZBOND_TICKER] = gewichte.get(_ERSATZBOND_TICKER, Decimal(0)) + ersatz_anteil
        return gewichte

    return replace(strategy, toepfe=toepfe, gewichte_fn=gewichte_fn)

# Wochen-Naeherung der klassischen 50-/200-Tage-Durchschnitte (5 Handelstage/Woche),
# fuer die wochenweise gefuehrte Kurshistorie - derselbe Ansatz wie beim Szenario
# "Charttechnik: SMA-Crossover" (scenarios.SMA_KURZ_WOCHEN/SMA_LANG_WOCHEN), hier aber
# rein fuer die Anzeige auf dem Wertverlauf der jeweiligen Strategie statt als
# Handelsregel (#31).
_SMA_KURZ_WOCHEN = 10
_SMA_LANG_WOCHEN = 40


def _moving_average(values: list[float], fenster: int) -> list[float | None]:
    """Gleitender Durchschnitt der letzten ``fenster`` Werte (inkl. aktuellem);
    ``None`` solange noch nicht genug Werte vorliegen."""
    ergebnis: list[float | None] = []
    summe = 0.0
    for i, wert in enumerate(values):
        summe += wert
        if i >= fenster:
            summe -= values[i - fenster]
        ergebnis.append(summe / fenster if i >= fenster - 1 else None)
    return ergebnis


def _f(value: Decimal) -> float:
    return float(value)


# --- Risikokennzahlen (#40) -------------------------------------------------------
#
# Ergaenzen die Rendite um Volatilitaet und Max Drawdown, damit Strategien mit sehr
# unterschiedlichem Risikoprofil (breit diversifiziert vs. konzentriert, ungetestete
# Chartsignale) nicht allein anhand der nominalen Rendite verglichen werden. Reine
# Anzeige-Ableitung aus der bereits von engine.simulate() gelieferten Wertreihe, keine
# eigene Simulation.


def _wochenrenditen(total_values: list[float]) -> list[float]:
    return [
        (total_values[i] - total_values[i - 1]) / total_values[i - 1]
        for i in range(1, len(total_values))
        if total_values[i - 1] > 0
    ]


def _volatilitaet_pct(total_values: list[float]) -> float:
    """Annualisierte Volatilitaet (Standardabweichung der Wochenrenditen * sqrt(52))
    in Prozent."""
    renditen = _wochenrenditen(total_values)
    if len(renditen) < 2:
        return 0.0
    return statistics.pstdev(renditen) * (52**0.5) * 100


def _max_drawdown_pct(total_values: list[float]) -> float:
    """Groesster Wertverlust vom bisherigen Hoechststand aus, in Prozent (>= 0)."""
    peak: float | None = None
    max_dd = 0.0
    for wert in total_values:
        if peak is None or wert > peak:
            peak = wert
        if peak and peak > 0:
            max_dd = max(max_dd, (peak - wert) / peak)
    return max_dd * 100


# --- Risikoadjustierte Kennzahlen: Sharpe & Sortino -------------------------------
#
# Ergaenzen Rendite/Volatilitaet/Max-Drawdown um ein Rendite-pro-Risiko-Verhaeltnis:
# eine hohe annualisierte Rendite bei ebenso hoher Volatilitaet (bzw. bei vielen
# schweren Verlustwochen) ist kein besseres Ergebnis als eine niedrigere Rendite bei
# geringem Risiko - genau das zeigt die nominale Rendite allein nicht.
#
# Rueckfallwert, wenn sich aus der Kurshistorie kein Zins ableiten laesst (siehe
# _risikofreier_zins_pct()). Bis #75 war dieser Wert die einzige Quelle - und mit
# 0,0 keine harmlose Vereinfachung: Sharpe/Sortino waren damit faktisch
# "Rendite ÷ Risiko" statt "UEBERrendite ÷ Risiko", und die Frage, die eine
# Sharpe-Ratio ueberhaupt beantwortet - werde ich fuer das eingegangene Risiko
# besser bezahlt als fuers risikolose Parken? - konnte per Konstruktion nie mit
# "nein" beantwortet werden. Der Fehler wirkte zudem ungleich: er hob Strategien
# mit geringer Volatilitaet (kleiner Nenner) staerker an, also gerade die
# defensiven, deren Rendite dem Geldmarkt am naechsten liegt.
# Einheit: Prozentpunkte p.a. (wie der Rueckgabewert von _risikofreier_zins_pct()).
_RISIKOFREIER_ZINS_PLATZHALTER = 0.0

# EUR-Geldmarkt-ETF (Xtrackers II EUR Overnight Rate, thesaurierend). Bildet per
# Definition den EUR-Tagesgeldsatz ab und liegt mit durchgehender Historie seit
# 2007 ohnehin in price_history.csv - seine eigene CAGR ueber exakt den
# ausgewerteten Zeitraum IST der passende risikofreie Zins. Das haelt die Kennzahl
# automatisch am jeweiligen Zinsumfeld (2006-2008 3-4%, 2012-2022 nahe 0%, ab 2023
# wieder 3-4%), statt einen festen Wert zu hinterlegen, der dagegen veraltet -
# dasselbe Prinzip wie auf der Praemissen-Seite (_praemissen_kontext()).
_GELDMARKT_TICKER = "XEON"

# Unter dieser Laenge ist eine annualisierte Geldmarktrendite aus so wenigen
# Wochen kein belastbarer Zins mehr - dann gilt der Rueckfallwert.
_ZINS_MIN_WOCHEN = 26


def _risikofreier_zins_pct(rows: list[PriceRow]) -> float:
    """Risikofreier Zins in % p.a., abgeleitet aus dem Geldmarkt-ETF ueber
    denselben Zeitraum, ueber den auch die Strategie ausgewertet wird (#75).

    Faellt auf ``_RISIKOFREIER_ZINS_PLATZHALTER`` zurueck, wenn der Ticker im
    Zeitraum fehlt oder der Zeitraum zu kurz ist - der Platzhalter bleibt also
    Rueckfallwert, ist aber nicht mehr die einzige Quelle.
    """
    kurse = [(r.date, r.prices[_GELDMARKT_TICKER]) for r in rows if _GELDMARKT_TICKER in r.prices]
    if len(kurse) < _ZINS_MIN_WOCHEN:
        return _RISIKOFREIER_ZINS_PLATZHALTER
    (erstes_datum, erster_kurs), (letztes_datum, letzter_kurs) = kurse[0], kurse[-1]
    if erster_kurs <= 0:
        return _RISIKOFREIER_ZINS_PLATZHALTER
    gesamt_pct = _f((letzter_kurs - erster_kurs) / erster_kurs) * 100
    return _cagr_pct(gesamt_pct, (letztes_datum - erstes_datum).days)


def _sharpe_ratio(total_values: list[float], risikofreier_zins_pct: float | None = None) -> float:
    """Annualisierte Ueberrendite je Einheit annualisierter Volatilitaet
    (Standardabweichung aller Wochenrenditen, positive wie negative)."""
    renditen = _wochenrenditen(total_values)
    if len(renditen) < 2:
        return 0.0
    std_pct = statistics.pstdev(renditen) * (52**0.5)
    if std_pct == 0:
        return 0.0
    # ACHTUNG Einheiten: _wochenrenditen() liefert BRUECHE (0,01 = 1%), std_pct und
    # ann_mean_pct sind trotz ihrer Namen ebenfalls Brueche. Der uebergebene Zins
    # kommt dagegen in Prozentpunkten p.a. herein (wie ueberall sonst in dieser
    # Datei) und muss deshalb vor dem Abzug umgerechnet werden.
    zins = _RISIKOFREIER_ZINS_PLATZHALTER if risikofreier_zins_pct is None else risikofreier_zins_pct
    ann_mean_pct = statistics.fmean(renditen) * 52
    return (ann_mean_pct - zins / 100) / std_pct


def _downside_deviation(renditen: list[float], ziel: float = 0.0) -> float:
    """Wurzel des mittleren quadrierten Unterschreitens von ``ziel`` ueber ALLE
    Wochen (nicht nur die negativen) - Standarddefinition der Sortino-Kennzahl."""
    if not renditen:
        return 0.0
    quadrate = [min(r - ziel, 0.0) ** 2 for r in renditen]
    return (sum(quadrate) / len(quadrate)) ** 0.5


def _sortino_ratio(total_values: list[float], risikofreier_zins_pct: float | None = None) -> float:
    """Wie ``_sharpe_ratio``, aber nur Verlustwochen fliessen ins Risikomass ein -
    Streuung nach oben (Gewinnwochen) wird nicht als Risiko gewertet."""
    renditen = _wochenrenditen(total_values)
    if len(renditen) < 2:
        return 0.0
    downside_pct = _downside_deviation(renditen) * (52**0.5)
    if downside_pct == 0:
        return 0.0
    # Einheiten wie bei _sharpe_ratio(): Brueche gegen Prozentpunkte, siehe dort.
    zins = _RISIKOFREIER_ZINS_PLATZHALTER if risikofreier_zins_pct is None else risikofreier_zins_pct
    ann_mean_pct = statistics.fmean(renditen) * 52
    return (ann_mean_pct - zins / 100) / downside_pct


# --- Walk-Forward-Robustheit ueber Teilperioden -----------------------------------
#
# Alle Szenarien in scenarios.py sind bislang "erster Ansatz, nicht optimiert" auf
# EINER einzigen, kurzen Kurshistorie durchgerechnet - eine gut aussehende Rendite
# koennte dort ebenso gut Zufall/Overfitting auf genau diesen Zeitraum sein wie ein
# echter Vorteil. Da die Regeln selbst keine an Daten gefitteten Parameter haben
# (kein Training im eigentlichen Sinn), ist eine klassische Train/Test-Aufteilung
# nicht anwendbar; stattdessen prueft dieser Abschnitt Konsistenz ueber die Zeit:
# dieselbe Strategie wird unabhaengig auf mehreren aufeinanderfolgenden Teilperioden
# JEWEILS FRISCH (eigenes Startkapital, keine fortgefuehrte Position) simuliert. Eine
# Strategie, die nur in einer Teilperiode stark performt und in den anderen schwach
# oder negativ, ist weniger vertrauenswuerdig als eine mit aehnlicher Rendite in
# jedem Segment - auch wenn die Gesamtrendite ueber die volle Historie identisch
# waere. Reine Anzeige-Ableitung: ruft engine.simulate() mehrfach mit Ausschnitten
# derselben Kurshistorie auf, keine eigene Berechnungslogik.
_WALK_FORWARD_SEGMENTE = 3
_WALK_FORWARD_MIN_WOCHEN_PRO_SEGMENT = 10


def _walk_forward_segmente(rows: list[PriceRow], strategy: Strategy) -> list[dict]:
    max_segmente = max(1, len(rows) // _WALK_FORWARD_MIN_WOCHEN_PRO_SEGMENT)
    segmente = min(_WALK_FORWARD_SEGMENTE, max_segmente)
    if segmente < 2:
        return []

    grenzen = [round(i * len(rows) / segmente) for i in range(segmente + 1)]
    ergebnisse = []
    for start, ende in zip(grenzen, grenzen[1:]):
        segment_rows = rows[start:ende]
        if len(segment_rows) < 2:
            continue
        rendite_pct = _rendite_pct(simulate(segment_rows, strategy), strategy)
        ergebnisse.append(
            {
                "label": f"{segment_rows[0].date.isoformat()} – {segment_rows[-1].date.isoformat()}",
                "rendite_pct": _f(rendite_pct),
                "rendite_label": f"{rendite_pct:+.2f}",
            }
        )
    return ergebnisse


# --- Zeitraum-Presets (#54) --------------------------------------------------------
#
# Wunsch: Betrachtungszeitraum im Dashboard clientseitig einstellbar machen. Da
# docs/*.html statische, bei jedem Build erzeugte Seiten ohne Backend sind (GitHub
# Pages), kann ein Zeitraumfilter nicht "live" neu simulieren - echte Steuer-/
# Rebalancing-Logik lässt sich nicht in JS nachbauen. Variante B (Owner-Entscheidung
# in #54, gegenüber reinem Chart-Zuschneiden): feste Zeitraum-Presets (1/3/5 Jahre
# sowie die gesamte Historie) werden beim Build je Strategie/Szenario VOLLSTÄNDIG
# NEU simuliert (frisches Startkapital, keine fortgeführte Position - analog
# ``_walk_forward_segmente()``), inklusive Rendite/Vola/Max-Drawdown/Sharpe/Sortino
# UND eigenem Wertverlauf-Chart. Die Seite liefert damit für jeden Preset einen
# fertigen Datensatz aus; ein Umschalter im Browser (reines JS, kein weiterer
# Server-Request) wechselt nur, welcher bereits vorhandene Datensatz angezeigt wird -
# "clientseitig einstellbar" im Sinne der Bedienung, nicht der Berechnung.
_ZEITRAUM_PRESETS: list[tuple[str, int | None]] = [
    ("1j", 1),
    ("3j", 3),
    ("5j", 5),
    ("alle", None),
]
_ZEITRAUM_PRESET_LABELS: dict[str, str] = {
    "1j": "1 Jahr",
    "3j": "3 Jahre",
    "5j": "5 Jahre",
    "alle": "Gesamte Historie",
}
# Englische Fassung fuers clientseitige Sprachumschalten (Dreipunktmenue).
_ZEITRAUM_PRESET_LABELS_EN: dict[str, str] = {
    "1j": "1 year",
    "3j": "3 years",
    "5j": "5 years",
    "alle": "Entire history",
}
_ERWEITERT_PRESET_LABEL_EN = "Extended (replacement bond assumption)"

# Englische Anzeigenamen der ueber alle Strategien/Szenarien hinweg verwendeten
# Topf-Namen (Chart-Legenden auf den Detailseiten) - rein darstellerisch, die
# Topf-Namen selbst (als dict-Schluessel in Strategy/engine.py) bleiben deutsch.
_TOPF_NAMEN_EN: dict[str, str] = {
    "Topf A - Sicherheit": "Bucket A - Safety",
    "Topf B - Wachstum": "Bucket B - Growth",
    "Topf C - Einzelaktien-Satellit": "Bucket C - Individual Stock Satellite",
    "Topf A - Benchmark": "Bucket A - Benchmark",
}


def _benchmark_reihen(rows: list[PriceRow], strategy: Strategy) -> list[dict]:
    """Wertverlauf je verfügbarem Benchmark aus `BENCHMARK_STRATEGIEN` (#72),
    simuliert über exakt dieselben `rows` und mit demselben Startkapital wie
    `strategy` - dadurch hat die Overlay-Reihe automatisch dieselbe Länge und
    denselben Startwert wie `strategy`s eigener Wertverlauf, ohne Datum-
    Abgleich per Hand (siehe engine.simulate(): ein Punkt in `value_history`
    je Zeile in `rows`, in derselben Reihenfolge).

    Ein Kandidat wird nur aufgenommen, wenn ALLE seine Ticker im übergebenen
    Zeitraum mindestens einen Kurs haben ("wenn im Portfolio vorhanden",
    #72) - sonst bliebe die Linie über den ganzen Zeitraum bei 0. Die
    Strategie selbst (z. B. SP500_BENCHMARK auf seiner eigenen Detailseite)
    wird ausgeschlossen, eine identische Linie als "Overlay" auf sich selbst
    wäre nur redundant."""
    overlays = []
    for bench in BENCHMARK_STRATEGIEN:
        if bench.name == strategy.name:
            continue
        bench_ticker = bench.alle_ticker_gewichte()
        if not all(any(t in row.prices for row in rows) for t in bench_ticker):
            continue
        bench_result = simulate(rows, replace(bench, startkapital=strategy.startkapital))
        overlays.append(
            {
                "id": _slug(bench.name),
                "label": bench.name,
                "total_values": [_f(vp.total_value) for vp in bench_result.value_history],
            }
        )
    return overlays


def _jahre_zurueck(stichtag: date, jahre: int) -> date:
    """``stichtag`` minus ``jahre`` volle Jahre - faellt bei einem nicht
    existierenden 29. Februar auf den 28. zurueck, statt eine Exception zu
    werfen."""
    try:
        return stichtag.replace(year=stichtag.year - jahre)
    except ValueError:
        return stichtag.replace(year=stichtag.year - jahre, day=28)


def _preset_eintrag(
    preset_id: str, label: str, preset_rows: list[PriceRow], strategy: Strategy, label_en: str | None = None
) -> dict:
    result = simulate(preset_rows, strategy)
    # Risikofreier Zins aus dem Geldmarkt-ETF ueber genau DIESEN Preset-Zeitraum
    # (#75) - ein 1-Jahres-Preset im Hochzinsumfeld hat einen anderen Referenzzins
    # als die volle Historie.
    zins_pct = _risikofreier_zins_pct(preset_rows)
    total_values = [_f(vp.total_value) for vp in result.value_history]
    labels = [vp.date.isoformat() for vp in result.value_history]
    rendite_pct = _rendite_pct(result, strategy)
    return {
        "id": preset_id,
        "label": label,
        "label_en": label_en or label,
        "rendite_pct": _f(rendite_pct),
        "rendite_label": f"{rendite_pct:+.2f}",
        "volatilitaet_label": f"{_volatilitaet_pct(total_values):.2f}",
        "max_drawdown_label": f"{-_max_drawdown_pct(total_values):.2f}",
        "sharpe_label": f"{_sharpe_ratio(total_values, zins_pct):.2f}",
        "sortino_label": f"{_sortino_ratio(total_values, zins_pct):.2f}",
        "risikofreier_zins_label": f"{zins_pct:.2f}".replace(".", ","),
        "labels": labels,
        "total_values": total_values,
        "chart_max": max(total_values) if total_values else 0.0,
        "benchmarks": _benchmark_reihen(preset_rows, strategy),
    }


def _zeitraum_presets(
    rows: list[PriceRow], strategy: Strategy, erweiterte_rows: list[PriceRow] | None = None
) -> list[dict]:
    letztes_datum = rows[-1].date
    presets = []
    for preset_id, jahre in _ZEITRAUM_PRESETS:
        if jahre is None:
            preset_rows = rows
        else:
            cutoff = _jahre_zurueck(letztes_datum, jahre)
            preset_rows = [r for r in rows if r.date >= cutoff]
        if len(preset_rows) < 1:
            continue
        presets.append(
            _preset_eintrag(
                preset_id,
                _ZEITRAUM_PRESET_LABELS[preset_id],
                preset_rows,
                strategy,
                _ZEITRAUM_PRESET_LABELS_EN[preset_id],
            )
        )
    # #80: zusaetzlicher Preset ueber die volle (nicht auf "alle Zielinstrumente
    # handelbar" zurechtgeschnittene) Historie, mit der Ersatzbond-Annahme statt
    # der Look-ahead-Vermeidung aus F4/#63 - nur anbieten, wenn dadurch
    # tatsaechlich mehr Historie zur Verfuegung steht, sonst waere er identisch
    # mit "Gesamte Historie" und nur verwirrende Redundanz.
    if erweiterte_rows is not None and erweiterte_rows and erweiterte_rows[0].date < rows[0].date:
        erweiterte_strategie = _mit_ersatzbond(strategy)
        presets.append(
            _preset_eintrag(
                "erweitert",
                "Erweitert (Ersatzbond-Annahme)",
                erweiterte_rows,
                erweiterte_strategie,
                _ERWEITERT_PRESET_LABEL_EN,
            )
        )
    return presets


# --- Eingefrorene Kurse (#42) ------------------------------------------------------


def _carry_forward_streaks(rows: list[PriceRow], fetch_log: list[FetchLogEntry]) -> dict[str, int]:
    """Je Ticker: Anzahl der zuletzt aufeinanderfolgenden Wochen (endend bei der
    letzten Kurszeile), in denen laut fetch_log.csv KEIN frischer Kurs abgerufen werden
    konnte (Status carried_forward/missing) - macht sichtbar, wenn eine flache
    Kursentwicklung einen fehlgeschlagenen Datenabruf statt echter Marktstille
    widerspiegelt."""
    stale = {(entry.date, entry.ticker) for entry in fetch_log if entry.status in _STALE_STATUS}
    tickers = {ticker for row in rows for ticker in row.prices}
    streaks: dict[str, int] = {}
    for ticker in tickers:
        wochen = 0
        for row in reversed(rows):
            if (row.date, ticker) not in stale:
                break
            wochen += 1
        streaks[ticker] = wochen
    return streaks


_UMLAUT_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _slug(name: str) -> str:
    normalisiert = name.lower().translate(_UMLAUT_TRANSLIT)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalisiert)).strip("-")


def _teilszenario_gruppen(views: list[dict], strategies: list[Strategy]) -> list[dict]:
    """Gruppiert Unterszenarien (``Strategy.teil_von``, #30) je übergeordneter
    Strategie für einen gemeinsamen Vergleichs-Chart auf der Startseite -
    generisch für jede zusammengesetzte Strategie (aktuell: die fünf
    einzelnen Börsenweisheiten-Szenarien unter "Börsenweisheiten (alle fünf
    kombiniert)"), keine eigene Simulationslogik. Liefert nur Gruppen, deren
    übergeordnete Strategie tatsächlich mitgerendert wird."""
    views_by_id = {v["id"]: v for v in views}
    kinder_je_eltern: dict[str, list[dict]] = {}
    for s in strategies:
        if s.teil_von is None:
            continue
        eltern_id = _slug(s.teil_von)
        kind_id = _slug(s.name)
        if eltern_id not in views_by_id or kind_id not in views_by_id:
            continue
        kinder_je_eltern.setdefault(s.teil_von, []).append(views_by_id[kind_id])

    gruppen = []
    for eltern_name, kinder in kinder_je_eltern.items():
        eltern_view = views_by_id[_slug(eltern_name)]
        mitglieder = [eltern_view] + kinder
        alle_werte = [wert for m in mitglieder for wert in m["total_values"]]
        gruppen.append(
            {
                "name": eltern_name,
                "id": _slug(eltern_name),
                "mitglieder": mitglieder,
                "chart_max": max(alle_werte) if alle_werte else 0.0,
                # #93: der Gruppen-Chart hing bisher als einziger Wertverlauf-Chart
                # nicht am Benchmark-Schalter (#72). Die Reihen der uebergeordneten
                # Strategie passen ohne Datumsabgleich, weil alle Mitglieder ueber
                # dieselben `rows` simuliert werden (gleiches Instrumentenset).
                "benchmarks_json": json.dumps(eltern_view["benchmarks"]),
            }
        )
    return gruppen


def _rendite_pct(result: SimulationResult, strategy: Strategy) -> Decimal:
    if strategy.startkapital <= 0:
        return Decimal(0)
    endwert = result.value_history[-1].total_value
    return ((endwert - strategy.startkapital) / strategy.startkapital) * 100


def _tage_zwischen(result: SimulationResult) -> int:
    points = result.value_history
    return (points[-1].date - points[0].date).days


def _cagr_pct(rendite_pct: float, tage: int) -> float:
    """Annualisierte Rendite (CAGR) aus der Gesamtrendite ueber ``tage`` Tage (F6b,
    #63) - ueber viele Jahre ist eine Gesamtrendite wie "+52.558,53%" praktisch
    unlesbar, waehrend eine annualisierte Zahl direkt geprueft werden kann. Dient
    zugleich als Basis fuer die Leave-one-out-Differenzen (F6c): die Differenz
    zweier CAGR-Werte bleibt eine sinnvolle Prozentpunkt-Angabe, auch wenn sich die
    zugrundeliegenden Gesamtrenditen um Groessenordnungen unterscheiden (z. B. durch
    eine einzelne dominante Position) - anders als die Differenz zweier
    Gesamtrenditen, die dort in absurde Groessenordnungen laufen kann."""
    if tage <= 0:
        return 0.0
    faktor = 1 + rendite_pct / 100
    if faktor <= 0:
        return -100.0
    jahre = tage / 365.25
    return (faktor ** (1 / jahre) - 1) * 100


# --- Gemeinsamer Vergleichszeitraum (#73, Risikokennzahlen #78) --------------------
#
# _real_investierbarer_zeitraum() (F4, #63) schneidet die Historie JE STRATEGIE auf
# den Zeitraum zu, ab dem deren komplettes Instrumentenset handelbar war. Das ist fuer
# sich genommen richtig - es nimmt den Look-ahead-Bias heraus -, erzeugt aber ein
# zweites, ebenso entscheidungsrelevantes Problem: die Zeitraeume unterscheiden sich
# dann zwischen den Strategien erheblich. Der S&P-500-Benchmark braucht nur IUSA und
# laeuft deshalb ueber die vollen 20 Jahre (ab 2006), waehrend jede Barbell-Strategie
# erst 2021 beginnt, weil ein einziges ihrer Instrumente nicht frueher existiert.
#
# Eine nach CAGR sortierte Uebersichtstabelle stellte damit eine 20-Jahres-Rendite
# (inkl. Finanzkrise und anschliessender Erholung) neben 5-Jahres-Renditen (inkl.
# Baerenmarkt 2022) - und ausgerechnet die eine Zeile, an der jede Anlageentscheidung
# haengt ("schlaegt die Strategie einfach den Index?"), war die nicht vergleichbare.
# Gemessen an der realen Historie drehte das die Aussage fuer sechs von 15
# Strategien/Szenarien.
#
# Diese Funktionen simulieren deshalb JEDE Strategie zusaetzlich auf dem spaetesten
# gemeinsamen Startdatum aller angezeigten Strategien - frisch, mit eigenem
# Startkapital, exakt nach demselben Muster wie _walk_forward_segmente() und
# _zeitraum_presets(). Die Uebersichtstabelle sortiert danach.
#
# #78: Das gilt seither fuer RENDITE UND RISIKO. Zunaechst waren nur die
# Renditespalten umgestellt, Volatilitaet/Max-Drawdown/Sharpe/Sortino blieben auf
# dem jeweils eigenen Zeitraum und wurden nur mit ausgewiesenem Zeitraum je Zeile
# gekennzeichnet. Das reichte nicht: der Effekt konzentriert sich fast vollstaendig
# auf die Benchmark-Zeile (alle uebrigen Strategien liegen ohnehin fast ganz im
# gemeinsamen Fenster), und dort war er gross genug, um die Aussage der Tabelle zu
# drehen - der Max Drawdown des S&P-500-Benchmarks stand bei -51,95% (enthaelt die
# Finanzkrise 2008) gegenueber rund -30% der Barbell-Strategien, auf gleicher Basis
# sind es -21,21%. Der Index sah damit als einziger sowohl renditeschwaecher als
# auch mit Abstand riskanter aus, war auf vergleichbarer Basis aber der mit dem
# GERINGSTEN Drawdown im Feld: aus scheinbarer Dominanz der Barbell-Strategien auf
# beiden Achsen wird ein echter Rendite-Risiko-Tausch - genau die Abwaegung, um die
# es bei einer Barbell-Strategie ueberhaupt geht. Auch ein Sharpe-Paarvergleich
# kippte (0,76 gegen 0,64 wurde zu 0,66 gegen 0,66), weil fuer die beiden Zeitraeume
# unterschiedliche risikofreie Zinsen gelten (0,76% ueber 20 Jahre, 2,05% ueber 5,
# siehe #75). Ein je Zeile ausgewiesener Zeitraum macht das nachvollziehbar, aber
# nicht aufloesbar - aus "2006-2026" laesst sich nicht ableiten, wie gross der
# Drawdown ohne 2008 gewesen waere.
#
# Die Uebersichtstabelle zeigt deshalb AUSSCHLIESSLICH Vergleichszeitraum-Werte
# (Owner-Entscheidung zu #78). Die Kennzahlen ueber den jeweils eigenen Zeitraum
# einer Strategie gehen dadurch nicht verloren: sie stehen weiterhin auf deren
# Detailseite im Abschnitt "Kennzahlen nach Betrachtungszeitraum" (#54), dessen
# Preset "Gesamte Historie" genau die volle eigene Historie abbildet.

# Unter dieser Laenge ist ein gemeinsamer Zeitraum keine belastbare Aussage mehr -
# dann entfaellt die Spalte, statt eine aus wenigen Wochen annualisierte Zahl zu
# zeigen (dieselbe Zurueckhaltung wie bei _walk_forward_segmente()).
_VERGLEICH_MIN_WOCHEN = 26


def _gemeinsamer_beginn(strategie_rows: dict[str, list[PriceRow]]) -> date | None:
    """Spaetestes Simulations-Startdatum ueber alle angezeigten Strategien - ab
    diesem Datum hat JEDE von ihnen Kurse und ist damit vergleichbar."""
    beginne = [rows[0].date for rows in strategie_rows.values() if rows]
    return max(beginne) if beginne else None


def _vergleichs_kennzahlen(
    strategy: Strategy, rows: list[PriceRow], beginn: date
) -> dict | None:
    """ALLE Kennzahlen dieser Strategie, frisch simuliert ab ``beginn`` - Rendite
    UND Risiko. ``None``, wenn der verbleibende Zeitraum zu kurz fuer eine
    belastbare Annualisierung ist.

    Lieferte bis #78 nur die CAGR und verwarf die Wertreihe des Vergleichslaufs,
    obwohl die Risikokennzahlen daraus ohne weitere Simulation ableitbar sind. Das
    war nicht bloss ungenutztes Potenzial, sondern liess die Uebersichtstabelle
    Rendite und Risiko aus zwei VERSCHIEDENEN Zeitraeumen nebeneinanderstellen -
    siehe den Kommentar an ``_VERGLEICH_MIN_WOCHEN`` oben und #78.

    Der risikofreie Zins fuer Sharpe/Sortino kommt aus genau diesem Ausschnitt
    (#75), nicht aus dem eigenen Zeitraum der Strategie - sonst waere die
    Ueberrendite gegen einen Zins gemessen, der in diesem Fenster gar nicht galt.
    """
    ausschnitt = [r for r in rows if r.date >= beginn]
    if len(ausschnitt) < _VERGLEICH_MIN_WOCHEN:
        return None
    result = simulate(ausschnitt, strategy)
    total_values = [_f(vp.total_value) for vp in result.value_history]
    zins_pct = _risikofreier_zins_pct(ausschnitt)
    return {
        "cagr_pct": _cagr_pct(_f(_rendite_pct(result, strategy)), _tage_zwischen(result)),
        "volatilitaet_pct": _volatilitaet_pct(total_values),
        "max_drawdown_pct": _max_drawdown_pct(total_values),
        "sharpe_ratio": _sharpe_ratio(total_values, zins_pct),
        "sortino_ratio": _sortino_ratio(total_values, zins_pct),
        "risikofreier_zins_pct": zins_pct,
    }


def _optimierungs_effekte(
    strategy: Strategy, rows: list[PriceRow], basis_cagr_pct: float, tage: int
) -> list[dict]:
    """Effekt jedes der fuenf Optimierungs-Schalter (#17) als Leave-one-out-Differenz
    auf CAGR-Basis (F6c, #63): CAGR mit allen Schaltern wie konfiguriert minus CAGR
    mit genau diesem einen Schalter aus. Eine Differenz zweier Gesamtrenditen ist
    hier keine sinnvolle Prozentpunkt-Angabe, sobald sich Basis- und Vergleichslauf
    um Groessenordnungen unterscheiden (z. B. durch Rebalancing in eine dominante
    Position wie Bitcoin) - CAGR bleibt in diesem Fall in einer plausiblen
    Groessenordnung."""
    effekte = []
    for feld, label in _OPTIMIERUNGS_LABELS.items():
        variante = replace(strategy.optimierungen, **{feld: False})
        ohne_result = simulate(rows, strategy, variante)
        ohne_rendite_pct = _rendite_pct(ohne_result, strategy)
        ohne_cagr_pct = _cagr_pct(_f(ohne_rendite_pct), tage)
        delta_pp = basis_cagr_pct - ohne_cagr_pct
        effekte.append(
            {
                "name": label,
                "name_en": _OPTIMIERUNGS_LABELS_EN[feld],
                "delta_pp": delta_pp,
                "delta_label": f"{delta_pp:+.2f}",
                "ohne_rendite_label": f"{ohne_rendite_pct:+.2f}",
                "ohne_cagr_label": f"{ohne_cagr_pct:+.2f}",
            }
        )
    return effekte


def _build_strategy_view(
    strategy: Strategy,
    result: SimulationResult,
    rows: list[PriceRow],
    carry_forward: dict[str, int] | None = None,
    erweiterte_rows: list[PriceRow] | None = None,
) -> dict:
    points = result.value_history
    labels = [vp.date.isoformat() for vp in points]
    total_values = [_f(vp.total_value) for vp in points]

    topf_names = [topf.name for topf in strategy.toepfe]
    topf_series = {name: [_f(vp.topf_weights.get(name, Decimal(0))) * 100 for vp in points] for name in topf_names}

    # Bei Szenario-Strategien (gewichte_fn gesetzt) sind die Ziel-Gewichte zeitabhängig -
    # fuer die Anzeige wird das Ziel-Regime der letzten Kurszeile herangezogen.
    if strategy.gewichte_fn is not None:
        ticker_targets = strategy.gewichte_fn(rows, len(rows) - 1)
    else:
        ticker_targets = strategy.alle_ticker_gewichte()
    topf_targets = {
        topf.name: _f(sum((ticker_targets.get(t, Decimal(0)) for t in topf.sub_gewichte), Decimal(0))) * 100
        for topf in strategy.toepfe
    }
    carry_forward = carry_forward or {}
    last = points[-1]
    holdings_table = []
    for ticker, units in sorted(result.holdings.items()):
        value = last.ticker_values.get(ticker, Decimal(0))
        price = (value / units) if units else Decimal(0)
        ist_gewicht = last.ticker_weights.get(ticker, Decimal(0)) * 100
        ziel_gewicht = ticker_targets.get(ticker, Decimal(0)) * 100
        abweichung_pp = ist_gewicht - ziel_gewicht
        carry_forward_wochen = carry_forward.get(ticker, 0)
        holdings_table.append(
            {
                "ticker": ticker,
                "units": f"{units:.4f}",
                "price": f"{price:.2f}",
                "value": f"{value:.2f}",
                "ist_gewicht": f"{ist_gewicht:.1f}",
                "ziel_gewicht": f"{ziel_gewicht:.1f}",
                "abweichung_pp_label": f"{abweichung_pp:+.1f}",
                # Warnung bei starker Abweichung eines EINZELNEN Instruments vom
                # Zielgewicht (#41) - der Rebalancing-Trigger prueft nur die
                # Topf-Ebene je Topf (5/25-Regel, #63), nicht die Konzentration
                # innerhalb eines Topfs. Nutzt bewusst dieselbe (absolute) Schwelle
                # wie das Topf-Rebalancing statt einer neu erfundenen Zahl.
                "konzentration_warnung": abs(abweichung_pp) > strategy.rebalancing_schwelle_pp,
                "carry_forward_wochen": carry_forward_wochen,
            }
        )

    gewinn = last.total_value - strategy.startkapital
    rendite_pct = _rendite_pct(result, strategy)
    tage = _tage_zwischen(result)
    cagr_pct = _cagr_pct(_f(rendite_pct), tage)
    # F6a (#63): geschaetzte Nettorendite neben der Bruttorendite. engine.py fuehrt
    # kumulierte_steuer bewusst nur als Tracking-Groesse (siehe engine.py-Kommentar
    # "reines Tracking") und zieht sie NICHT vom simulierten Depotwert ab - die
    # Bruttorendite oben ist deshalb eine Vor-Steuer-Zahl. Diese Naeherung zieht die
    # kumulierte Steuer einmalig am Ende vom Endwert ab, statt die Engine
    # umzubauen: eine Vereinfachung, weil tatsaechliche Steuerzahlungen unterjaehrig
    # und nicht als einmaliger Abzug am Simulationsende faellig werden.
    if strategy.startkapital > 0:
        netto_endwert = last.total_value - result.tax_status.kumulierte_steuer
        netto_rendite_pct = (netto_endwert - strategy.startkapital) / strategy.startkapital * 100
    else:
        netto_rendite_pct = Decimal(0)
    netto_cagr_pct = _cagr_pct(_f(netto_rendite_pct), tage)
    # Sofortverkauf zum Stichtag der letzten Kurszeile: anders als die "geschätzte
    # Nettorendite" oben (die nur die bereits TATSÄCHLICH realisierte Steuer vom
    # Bruttoendwert abzieht) zieht result.liquidationswert_nach_steuer zusätzlich
    # Ordergebühren und Steuer auf die bislang UNREALISIERTEN Gewinne jeder noch
    # gehaltenen Position ab (engine.simulate(), berechnet auf Kopien des
    # Steuerledgers - siehe Kommentar dort) - die realistischere Antwort auf "was
    # bleibt vom eingesetzten Kapital, wenn ich heute alles verkaufe".
    liquidationswert = result.liquidationswert_nach_steuer
    if strategy.startkapital > 0:
        liquidations_rendite_pct = (liquidationswert - strategy.startkapital) / strategy.startkapital * 100
    else:
        liquidations_rendite_pct = Decimal(0)
    volatilitaet_pct = _volatilitaet_pct(total_values)
    max_drawdown_pct = _max_drawdown_pct(total_values)
    risikofreier_zins_pct = _risikofreier_zins_pct(rows)
    sharpe_ratio = _sharpe_ratio(total_values, risikofreier_zins_pct)
    sortino_ratio = _sortino_ratio(total_values, risikofreier_zins_pct)
    cash_max_pct, cash_max_datum = _cash_anteil_max(points)
    walk_forward_segmente = _walk_forward_segmente(rows, strategy)
    walk_forward_spread_pp = (
        max(seg["rendite_pct"] for seg in walk_forward_segmente)
        - min(seg["rendite_pct"] for seg in walk_forward_segmente)
        if walk_forward_segmente
        else 0.0
    )
    zeitraum_presets = _zeitraum_presets(rows, strategy, erweiterte_rows)
    benchmarks = _benchmark_reihen(rows, strategy)

    # Leave-one-out: Einzeleffekt jeder Teilregel als Differenz zur Variante ohne sie,
    # auf CAGR-Basis (F6c, #63) - siehe _optimierungs_effekte() fuer die Begruendung.
    beitraege = []
    for beitrag in strategy.beitraege:
        ohne_result = simulate(rows, beitrag.ohne)
        ohne_rendite_pct = _rendite_pct(ohne_result, beitrag.ohne)
        ohne_cagr_pct = _cagr_pct(_f(ohne_rendite_pct), tage)
        delta_pp = cagr_pct - ohne_cagr_pct
        beitraege.append(
            {
                "name": beitrag.name,
                "delta_pp": delta_pp,
                "delta_label": f"{delta_pp:+.2f}",
                "ohne_rendite_label": f"{ohne_rendite_pct:+.2f}",
                "ohne_cagr_label": f"{ohne_cagr_pct:+.2f}",
            }
        )

    return {
        "name": result.strategy_name,
        "id": _slug(result.strategy_name),
        "beschreibung": strategy.beschreibung,
        "beschreibung_en": strategy.beschreibung_en or strategy.beschreibung,
        "rendite_pct": _f(rendite_pct),
        "rendite_pct_label": f"{rendite_pct:+.2f}",
        "cagr_pct": cagr_pct,
        "cagr_label": f"{cagr_pct:+.2f}",
        "netto_rendite_pct_label": f"{netto_rendite_pct:+.2f}",
        "netto_cagr_label": f"{netto_cagr_pct:+.2f}",
        "liquidationswert_label": f"{liquidationswert:.2f}",
        "liquidationssteuer_label": f"{result.liquidationssteuer:.2f}",
        "liquidationsgebuehren_label": f"{result.liquidationsgebuehren:.2f}",
        "liquidations_rendite_pct_label": f"{liquidations_rendite_pct:+.2f}",
        "gewinn_label": f"{gewinn:+.2f}",
        "volatilitaet_pct": volatilitaet_pct,
        "volatilitaet_label": f"{volatilitaet_pct:.2f}",
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_label": f"{-max_drawdown_pct:.2f}" if max_drawdown_pct else "0.00",
        "sharpe_ratio": sharpe_ratio,
        "sharpe_label": f"{sharpe_ratio:.2f}",
        "sortino_ratio": sortino_ratio,
        "sortino_label": f"{sortino_ratio:.2f}",
        # #75: je Strategie ausgewiesen, weil er aus deren eigenem
        # Simulationszeitraum abgeleitet wird und deshalb nicht fuer alle gleich ist.
        "risikofreier_zins_label": f"{risikofreier_zins_pct:.2f}".replace(".", ","),
        "cash_max_pct": cash_max_pct,
        "cash_max_label": f"{cash_max_pct:.1f}",
        "cash_max_datum": cash_max_datum or "–",
        "sim_beginn": points[0].date.isoformat(),
        # #73: der tatsaechliche Zeitraum gehoert neben jede Renditezahl - ohne ihn
        # ist nicht erkennbar, dass zwei Zeilen derselben Tabelle unterschiedlich
        # lange und unterschiedliche Marktphasen abdecken koennen.
        "sim_ende": points[-1].date.isoformat(),
        "sim_jahre_label": f"{tage / 365.25:.1f}".replace(".", ","),
        "sim_ende": points[-1].date.isoformat(),
        "teil_von": strategy.teil_von,
        "labels_json": json.dumps(labels),
        "total_values": total_values,
        "total_values_json": json.dumps(total_values),
        "sma_kurz_json": json.dumps(_moving_average(total_values, _SMA_KURZ_WOCHEN)),
        "sma_lang_json": json.dumps(_moving_average(total_values, _SMA_LANG_WOCHEN)),
        "topf_series_json": json.dumps(topf_series),
        "topf_targets": topf_targets,
        "topf_series_last": {name: series[-1] if series else 0.0 for name, series in topf_series.items()},
        "rebalancing_schwelle_pp": f"{strategy.rebalancing_schwelle_pp}",
        # Numerische Zweitfassungen fuer die Learnings-Ableitung (die uebrigen
        # Felder sind bereits fuer die Anzeige formatierte Strings).
        "startkapital_num": _f(strategy.startkapital),
        "steuer_num": _f(result.tax_status.kumulierte_steuer),
        "holdings_table": holdings_table,
        "total_value": f"{last.total_value:.2f}",
        "startkapital": f"{strategy.startkapital:.2f}",
        "trade_count": len(result.trades),
        "tax": {
            "year": result.tax_status.year,
            "freibetrag_verbraucht": f"{result.tax_status.freibetrag_verbraucht:.2f}",
            "freibetrag_verbleibend": f"{result.tax_status.freibetrag_verbleibend:.2f}",
            "verlustvortrag": f"{result.tax_status.verlustvortrag:.2f}",
            "kumulierte_steuer": f"{result.tax_status.kumulierte_steuer:.2f}",
        },
        "last_rebalance_date": result.last_rebalance_date.isoformat() if result.last_rebalance_date else "-",
        "last_harvest_date": result.last_harvest_date.isoformat() if result.last_harvest_date else "-",
        "beitraege": beitraege,
        "optimierungs_effekte": _optimierungs_effekte(strategy, rows, cagr_pct, tage),
        "walk_forward_segmente": walk_forward_segmente,
        "walk_forward_spread_label": f"{walk_forward_spread_pp:.2f}",
        "zeitraum_presets": zeitraum_presets,
        "zeitraum_presets_json": json.dumps(zeitraum_presets),
        # Benchmark-Overlay-Schalter (#72): Wertverlauf je verfügbarem Benchmark
        # über die volle (bereits real-investierbar zugeschnittene) Historie, eine
        # weitere Fassung je Zeitraum-Preset steckt bereits in "zeitraum_presets"
        # oben. Nur Vergleichslinie, fließt nirgends in Kennzahlen ein.
        "benchmarks": benchmarks,
        "benchmarks_json": json.dumps(benchmarks),
        # Eigene Y-Achsen-Skalierung statt des gemeinsamen Chart-Maximums (siehe
        # Strategy.eigene_chart_skala) - own_chart_max ist dabei bewusst NUR das
        # Maximum der eigenen Wertreihe, unabhaengig von allen anderen Strategien.
        "eigene_chart_skala": strategy.eigene_chart_skala,
        "im_startseiten_chart": strategy.im_startseiten_chart,
        "own_chart_max": max(total_values) if total_values else 0.0,
    }


def _cash_anteil_max(points: list) -> tuple[float, str | None]:
    """Größter Anteil ungenutzten (nicht in ein Instrument investierten)
    Kapitals über den gesamten Wertverlauf, in Prozent, plus das Datum dieses
    Höchststands. Cash ist hier ausschließlich der technische
    ``pending_cash``-Zustand aus ``engine.py`` (Kapitalanteil ohne
    handelbares Ziel) - siehe README „No separate cash position": die
    Strategien selbst kennen keine Cash-Zielallokation, Topf A übernimmt
    diese Rolle."""
    best_pct = 0.0
    best_datum: str | None = None
    for vp in points:
        if vp.total_value <= 0:
            continue
        invested = sum(vp.ticker_values.values(), Decimal(0))
        pct = float((vp.total_value - invested) / vp.total_value * 100)
        if pct > best_pct:
            best_pct = pct
            best_datum = vp.date.isoformat()
    return best_pct, best_datum


def _erste_kurse(rows: list[PriceRow]) -> dict[str, str]:
    """Datum der ersten Kurszeile je Ticker - macht sichtbar, ab wann ein
    Instrument in der Simulation überhaupt handelbar war."""
    erste: dict[str, str] = {}
    for row in rows:
        for ticker in row.prices:
            if ticker not in erste:
                erste[ticker] = row.date.isoformat()
    return erste


def _allokierte_ticker(strategies: list[Strategy]) -> set[str]:
    """Menge aller Ticker, die in mindestens einer Strategie/einem Szenario
    tatsächlich einem Topf zugeordnet sind (#66) - generisch aus
    ``Strategy.alle_ticker_gewichte()`` abgeleitet statt hart als Zahl "17"
    eingetragen, damit ein künftiger Instrumente- oder Strategiewechsel
    Dashboard und Prämissen-Seite automatisch mitzieht. `instruments.TICKERS`
    kann mehr Ticker enthalten als hier zurückkommen - die Differenz sind
    bewusst nicht allokierte Datenreihen (siehe instruments.py-Kommentar zu
    IUSA/XEON/EXSA/IBCL/IBCI/IQQ6/EXXY, #64/#65): Ticker, die
    ``engine.simulate()`` nie handelt, weil sie in keinem Topf liegen."""
    return {t for s in strategies for t in s.alle_ticker_gewichte()}


def _dividendenrendite_pct(inst: Instrument) -> float:
    """Ausschuettungsrendite eines Instruments in Prozent (#74) - der
    instrumenteneigene Wert, sonst der Pauschal-Platzhalter."""
    rendite = inst.dividendenrendite if inst.dividendenrendite is not None else DIVIDENDENRENDITE_PLATZHALTER
    return float(rendite) * 100


def _praemissen_kontext(rows: list[PriceRow], strategies: list[Strategy], views: list[dict]) -> dict:
    """Baut die Daten für die Prämissen-Seite.

    Bewusst durchgehend aus den tatsächlich verwendeten Konstanten,
    ``instruments.py``, der Kurshistorie und den bereits berechneten
    Strategie-``views`` abgeleitet - nach demselben Prinzip wie
    ``learnings.py``: nichts hier ist hinterlegter Text, der gegenüber dem
    Code veralten könnte. Ändert sich z. B. ``ORDERGEBUEHR`` oder die
    Teilfreistellung eines Instruments, ändert sich diese Seite mit. Die
    ``views`` (statt einer erneuten Simulation) liefern insbesondere den
    tatsächlichen Cash-Höchststand je Strategie/Szenario - siehe „No
    separate cash position" (README, #35): die Strategien kennen keine
    eigene Cash-Zielallokation, `cash_max_pct` misst deshalb ausschließlich
    den technischen `pending_cash`-Zustand (Kapital ohne handelbares Ziel,
    #55)."""
    views_by_id = {v["id"]: v for v in views}
    erste = _erste_kurse(rows)
    allokierte_ticker = _allokierte_ticker(strategies)
    instrumente = []
    nicht_allokierte_instrumente = []
    for ticker in TICKERS:
        inst = INSTRUMENTS[ticker]
        eintrag = {
            "ticker": ticker,
            "name": inst.name,
            "isin": inst.isin or "–",
            "teilfreistellung": f"{inst.teilfreistellung * 100:.0f}",
            "thesaurierend": "ja" if inst.thesaurierend else "nein",
            "thesaurierend_en": "yes" if inst.thesaurierend else "no",
            "ausschuettend": "ja" if inst.ausschuettend else "nein",
            "ausschuettend_en": "yes" if inst.ausschuettend else "no",
            # #74: je Instrument statt eines Pauschalwerts fuer alle. "-" fuer
            # Instrumente, die nicht ausschuetten; der Platzhalter erscheint nur
            # dort, wo tatsaechlich kein instrumenteneigener Wert hinterlegt ist.
            "dividendenrendite": (
                f"{_dividendenrendite_pct(inst):.1f}".replace(".", ",")
                if inst.ausschuettend
                else "–"
            ),
            "dividendenrendite_platzhalter": inst.ausschuettend and inst.dividendenrendite is None,
            # #76: laufende Fondskosten. "-" fuer Instrumente ohne Fondsmantel
            # (Einzelaktien, physisches Gold, BTC).
            "ter": f"{float(inst.ter) * 100:.2f}".replace(".", ",") if inst.ter else "–",
            "spekulationsfrist": (
                f"{inst.spekulationsfrist_tage} Tage" if inst.spekulationsfrist_tage else "–"
            ),
            "spekulationsfrist_en": (
                f"{inst.spekulationsfrist_tage} days" if inst.spekulationsfrist_tage else "–"
            ),
            "erster_kurs": erste.get(ticker, "– (nie)"),
            "fehlt_anfangs": erste.get(ticker) != rows[0].date.isoformat(),
        }
        if ticker in allokierte_ticker:
            instrumente.append(eintrag)
        else:
            nicht_allokierte_instrumente.append(eintrag)

    strategie_liste = []
    for s in strategies:
        slug = _slug(s.name)
        view = views_by_id.get(slug, {})
        strategie_liste.append(
            {
                "name": s.name,
                "id": slug,
                "startkapital": f"{s.startkapital:.2f}",
                "schwelle": f"{s.rebalancing_schwelle_pp}",
                "schwelle_relativ": f"{s.rebalancing_schwelle_relativ * 100:.0f}",
                "ziel_topf": s.ziel_topf,
                "ziel_topf_en": _TOPF_NAMEN_EN.get(s.ziel_topf, s.ziel_topf),
                "ziel_gewicht": f"{s.ziel_gewicht * 100:.0f}",
                "dynamisch": "ja" if s.gewichte_fn is not None else "nein",
                "dynamisch_en": "yes" if s.gewichte_fn is not None else "no",
                "toepfe": [
                    {"name": t.name, "gewicht": f"{t.gewicht_gesamt * 100:.0f}"} for t in s.toepfe
                ],
                "cash_max_label": view.get("cash_max_label", "0.0"),
                "cash_max_datum": view.get("cash_max_datum", "–"),
                "sim_beginn": view.get("sim_beginn", "–"),
                "risikofreier_zins_label": view.get("risikofreier_zins_label", "–"),
            }
        )
    cash_werte = [s["cash_max_label"] for s in strategie_liste]
    cash_ueberall_null = all(float(v.replace(",", ".")) == 0.0 for v in cash_werte)

    return {
        "instrumente": instrumente,
        "nicht_allokierte_instrumente": nicht_allokierte_instrumente,
        "strategien": strategie_liste,
        "zeitraum_von": rows[0].date.isoformat(),
        "zeitraum_bis": rows[-1].date.isoformat(),
        "wochen": len(rows),
        "ordergebuehr": f"{ORDERGEBUEHR:.2f}",
        "steuersatz": f"{STEUERSATZ * 100:.3f}".rstrip("0").rstrip("."),
        "sparerpauschbetrag": f"{SPARERPAUSCHBETRAG_PRO_JAHR:.0f}",
        "spek_freigrenze": f"{SPEKULATIONSFRIST_FREIGRENZE_PRO_JAHR:.0f}",
        "vorabpauschale_basiszins": f"{VORABPAUSCHALE_BASISZINS_PLATZHALTER * 100:.1f}".replace(".", ","),
        "vorabpauschale_faktor": f"{VORABPAUSCHALE_FAKTOR * 100:.0f}",
        "dividendenrendite": f"{DIVIDENDENRENDITE_PLATZHALTER * 100:.1f}".replace(".", ","),
        # Nur noch der Rueckfallwert (#75) - der tatsaechlich verwendete Zins steht
        # je Strategie in der Tabelle oben.
        "risikofreier_zins": f"{_RISIKOFREIER_ZINS_PLATZHALTER:.0f}",
        "geldmarkt_ticker": _GELDMARKT_TICKER,
        "sma_kurz": _SMA_KURZ_WOCHEN,
        "sma_lang": _SMA_LANG_WOCHEN,
        "walk_forward_segmente": _WALK_FORWARD_SEGMENTE,
        "walk_forward_min_wochen": _WALK_FORWARD_MIN_WOCHEN_PRO_SEGMENT,
        "cash_ueberall_null": cash_ueberall_null,
        "btc_fruehphase_ende": _BTC_FRUEHPHASE_ENDE.isoformat(),
        # #93: das Kombinationsverfahren zusammengesetzter Strategien gehoert
        # nachpruefbar auf diese Seite - der Startseiten-Abschnitt verweist
        # darauf, statt es dort in einem Absatz zu erklaeren. Die Regelnamen
        # kommen aus `Strategy.beitraege`, damit die Liste nicht gegenueber
        # scenarios.py veraltet.
        "zusammengesetzte": [
            {
                "name": s_.name,
                "id": _slug(s_.name),
                "regeln": [b.name for b in s_.beitraege],
            }
            for s_ in strategies
            if s_.beitraege
        ],
    }


def build_dashboard(
    price_history: list[PriceRow],
    strategies: list[Strategy] | None = None,
    output_path: Path = DEFAULT_OUTPUT,
    fetch_log: list[FetchLogEntry] | None = None,
) -> Path:
    """Baut die Startseite (nur Wertverlauf je Strategie/Szenario) sowie je eine
    Detailseite pro Strategie/Szenario (alles andere, inkl. 50-/200-Tage-Näherung) -
    siehe #31. Die Detailseiten landen als ``<slug>.html`` neben ``output_path``.

    ``fetch_log`` (optional, aus ``history_store.read_fetch_log()``) macht sichtbar,
    welche Instrumente zuletzt mit einem eingefrorenen statt frisch abgerufenen Kurs
    bewertet wurden (#42)."""
    if not price_history:
        raise ValueError("Kurshistorie ist leer - kein Dashboard erzeugbar")
    strategies = strategies if strategies is not None else STRATEGIES
    rows = sorted(price_history, key=lambda r: r.date)
    carry_forward = _carry_forward_streaks(rows, fetch_log or [])

    # F4 (#63): BTC-Fruehphase ausklammern, dann je Strategie erst dort simulieren,
    # wo ihr komplettes Instrumentenset tatsaechlich handelbar war - siehe
    # _ohne_btc_fruehphase()/_real_investierbarer_zeitraum(). carry_forward bleibt
    # bewusst auf der vollen, unveraenderten Historie berechnet (betrifft nur die
    # juengsten Wochen).
    rows_ohne_btc_fruehphase = _ohne_btc_fruehphase(rows)
    strategie_rows = {s.name: _real_investierbarer_zeitraum(rows_ohne_btc_fruehphase, s) for s in strategies}

    views = [
        _build_strategy_view(
            s,
            simulate(strategie_rows[s.name], s),
            strategie_rows[s.name],
            carry_forward,
            erweiterte_rows=rows_ohne_btc_fruehphase,
        )
        for s in strategies
    ]
    # Gemeinsamer Vergleichszeitraum (#73): jede Strategie zusaetzlich ab dem
    # spaetesten Startdatum aller angezeigten Strategien simulieren, damit die
    # Uebersichtstabelle Gleiches mit Gleichem vergleicht. Siehe _gemeinsamer_beginn().
    beginn = _gemeinsamer_beginn(strategie_rows)
    benchmark_namen = {b.name for b in BENCHMARK_STRATEGIEN}
    vergleich: dict[str, dict | None] = {}
    if beginn is not None:
        for s in strategies:
            vergleich[s.name] = _vergleichs_kennzahlen(s, strategie_rows[s.name], beginn)
    # Referenzlinie fuer die Ueberrendite: die erste Benchmark-Strategie, die im
    # gemeinsamen Zeitraum ueberhaupt eine Zahl liefert. Steht keine zur Verfuegung
    # (kein Benchmark unter den angezeigten Strategien), entfaellt die Spalte still,
    # statt gegen eine willkuerliche andere Strategie zu vergleichen.
    benchmark_cagr: float | None = None
    benchmark_label: str | None = None
    for s in strategies:
        kennzahlen = vergleich.get(s.name)
        if s.name in benchmark_namen and kennzahlen is not None:
            benchmark_cagr = kennzahlen["cagr_pct"]
            benchmark_label = s.name
            break

    for view in views:
        k = vergleich.get(view["name"])
        wert = k["cagr_pct"] if k is not None else None
        view["vergleich_cagr_pct"] = wert
        view["vergleich_cagr_label"] = f"{wert:+.2f}" if wert is not None else "–"
        # #78: Auch die Risikokennzahlen der UEBERSICHT stammen aus dem gemeinsamen
        # Zeitraum - aber bewusst unter EIGENEN Feldnamen. Die gleichnamigen Felder
        # ohne Praefix beschreiben weiterhin den eigenen Zeitraum der Strategie und
        # werden anderswo gebraucht: die Detailseite nutzt sie als Startwerte der
        # Kacheln im Abschnitt "Kennzahlen nach Betrachtungszeitraum" (#54, Preset
        # "Gesamte Historie"), die Praemissen-Seite den risikofreien Zins der
        # eigenen Simulation. Sie hier zu ueberschreiben wuerde beide verfaelschen.
        view["vergleich_volatilitaet_label"] = (
            f"{k['volatilitaet_pct']:.2f}" if k is not None else None
        )
        view["vergleich_max_drawdown_pct"] = k["max_drawdown_pct"] if k is not None else None
        view["vergleich_max_drawdown_label"] = (
            (f"{-k['max_drawdown_pct']:.2f}" if k["max_drawdown_pct"] else "0.00")
            if k is not None
            else None
        )
        view["vergleich_sharpe_ratio"] = k["sharpe_ratio"] if k is not None else None
        view["vergleich_sharpe_label"] = f"{k['sharpe_ratio']:.2f}" if k is not None else None
        view["vergleich_sortino_ratio"] = k["sortino_ratio"] if k is not None else None
        view["vergleich_sortino_label"] = f"{k['sortino_ratio']:.2f}" if k is not None else None
        if wert is None or benchmark_cagr is None or view["name"] in benchmark_namen:
            view["alpha_pp"] = None
            view["alpha_pp_label"] = "–"
        else:
            view["alpha_pp"] = wert - benchmark_cagr
            view["alpha_pp_label"] = f"{wert - benchmark_cagr:+.2f}"

    # Der risikofreie Zins des Vergleichszeitraums gilt fuer ALLE Zeilen gleich (es
    # ist derselbe Zeitraum) - deshalb einmal im Seitenkontext statt je View.
    vergleich_zins = next(
        (k["risikofreier_zins_pct"] for k in vergleich.values() if k is not None), None
    )
    vergleich_kontext = {
        "vergleich_zins_label": (
            f"{vergleich_zins:.2f}".replace(".", ",") if vergleich_zins is not None else None
        ),
        "vergleich_beginn": beginn.isoformat() if beginn is not None else None,
        "vergleich_ende": rows[-1].date.isoformat(),
        "vergleich_benchmark": benchmark_label,
        "vergleich_benchmark_label": (
            f"{benchmark_cagr:+.2f}" if benchmark_cagr is not None else None
        ),
        "vergleich_verfuegbar": any(v["vergleich_cagr_pct"] is not None for v in views),
    }

    # Sortierung nach der Rendite im GEMEINSAMEN Zeitraum (#73), sonst - solange
    # keiner ermittelbar ist - weiterhin nach CAGR ueber den jeweils eigenen
    # Zeitraum (F6b, #63).
    if vergleich_kontext["vergleich_verfuegbar"]:
        summary = sorted(
            views,
            key=lambda v: (v["vergleich_cagr_pct"] is not None, v["vergleich_cagr_pct"] or 0.0),
            reverse=True,
        )
    else:
        summary = sorted(views, key=lambda v: v["cagr_pct"], reverse=True)
    learnings = derive_learnings(views)
    teilszenario_gruppen = _teilszenario_gruppen(views, strategies)

    # Gemeinsames Y-Achsen-Maximum ueber alle Wertverlauf-Charts (#24): ohne das skaliert
    # jeder Chart unabhaengig, wodurch unterschiedliche Strategien optisch nicht mehr
    # vergleichbar sind. Strategien mit `eigene_chart_skala=True` (z. B. der
    # SP500_BENCHMARK, dessen Endwert ein Vielfaches der uebrigen betraegt) fliessen
    # NICHT in dieses gemeinsame Maximum ein, sonst wuerden alle anderen Charts durch
    # sie flachgedrueckt - sie bekommen stattdessen ihr eigenes Maximum (siehe
    # "chart_max"/"eigene_chart_skala" je View unten).
    eigene_skala_namen = {s.name for s in strategies if s.eigene_chart_skala}
    alle_werte = [
        wert for view in views for wert in view["total_values"] if view["name"] not in eigene_skala_namen
    ]
    wert_chart_max = max(alle_werte) if alle_werte else 0.0

    # Benchmark-Overlay-Schalter (#72): Union der auf irgendeiner Seite tatsächlich
    # verfügbaren Benchmarks (id + Anzeigename), fürs Rendern der Schalter-Buttons.
    # Ein Kandidat aus BENCHMARK_STRATEGIEN, der für keine einzige Strategie Kursdaten
    # hat (z. B. weil sein Ticker noch gar nicht in instruments.py steht), taucht hier
    # gar nicht erst auf - der Schalter zeigt dann nur "Kein Benchmark" plus die
    # tatsächlich nutzbaren Optionen.
    benchmark_optionen: dict[str, str] = {}
    for view in views:
        for bench in view["benchmarks"]:
            benchmark_optionen[bench["id"]] = bench["label"]

    common_context = dict(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        row_count=len(price_history),
        last_date=price_history[-1].date.isoformat(),
        # #66: aus den tatsächlich allokierten Tickern abgeleitet statt hart
        # eingetragen, damit die Zahl auf jeder Seite automatisch mit einem
        # künftigen Instrumente-/Strategiewechsel mitzieht.
        instrumente_anzahl=len(_allokierte_ticker(strategies)),
        # #79: alle abgerufenen Instrumente (Ticker + ausgeschriebener Name) auf
        # der Startseite, generisch aus instruments.TICKERS/INSTRUMENTS abgeleitet
        # statt hinterlegt - zieht bei einer künftigen Instrumentenänderung
        # automatisch mit, statt wie die README-Tabelle von Hand nachgezogen zu
        # werden. Bewusst ALLE Ticker (nicht nur die allokierten, #66): die
        # Frage "was wird da wöchentlich abgerufen?" ist unabhängig davon, ob
        # ein Instrument aktuell in einer Strategie steckt.
        portfolio_instrumente=[
            {"ticker": t, "name": INSTRUMENTS[t].name} for t in TICKERS
        ],
        benchmark_optionen=[{"id": k, "label": v} for k, v in benchmark_optionen.items()],
        topf_namen_en_json=json.dumps(_TOPF_NAMEN_EN),
        **vergleich_kontext,
    )

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    index_template = env.get_template("dashboard.html.j2")
    index_html = index_template.render(
        strategies=views,
        summary=summary,
        # #92: das CAGR-Balkendiagramm der Startseite zeigt nur die Strategien
        # mit `im_startseiten_chart` - mit allen Läufen standen dort mehr Balken
        # als Achsenbeschriftungen (Chart.js duennt die Beschriftungen aus). Die
        # vollstaendige Liste bleibt in `summary` (Tabelle auf vergleich.html).
        chart_summary=[v for v in summary if v["im_startseiten_chart"]],
        learnings=learnings,
        wert_chart_max=wert_chart_max,
        teilszenario_gruppen=teilszenario_gruppen,
        **common_context,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(index_html, encoding="utf-8")

    detail_template = env.get_template("strategy_detail.html.j2")
    for view in views:
        detail_html = detail_template.render(s=view, **common_context)
        detail_path = output_path.parent / f"{view['id']}.html"
        detail_path.write_text(detail_html, encoding="utf-8")

    # #88: Vergleichstabelle und Portfolio-Uebersicht stehen nicht mehr auf der
    # Startseite, sondern als eigene, ueber das Drei-Punkt-Menue erreichbare
    # Seiten - die Startseite bleibt damit auf Einleitung, Key Learnings und
    # Wertverlaeufe beschraenkt. Beide Seiten leiten sich vollstaendig aus
    # denselben Views/common_context ab, es wird nichts zusaetzlich simuliert.
    vergleich_html = env.get_template("vergleich.html.j2").render(
        summary=summary, **common_context
    )
    (output_path.parent / "vergleich.html").write_text(vergleich_html, encoding="utf-8")

    portfolio_html = env.get_template("portfolio.html.j2").render(**common_context)
    (output_path.parent / "portfolio.html").write_text(portfolio_html, encoding="utf-8")

    praemissen_html = env.get_template("praemissen.html.j2").render(
        **_praemissen_kontext(rows, strategies, views), **common_context
    )
    (output_path.parent / "praemissen.html").write_text(praemissen_html, encoding="utf-8")

    return output_path
