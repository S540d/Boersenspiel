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
from .instruments import INSTRUMENTS, TICKERS
from .learnings import derive_learnings
from .strategies import (
    DIVIDENDENRENDITE_PLATZHALTER,
    ORDERGEBUEHR,
    SPARERPAUSCHBETRAG_PRO_JAHR,
    SPEKULATIONSFRIST_FREIGRENZE_PRO_JAHR,
    STEUERSATZ,
    STRATEGIES,
    VORABPAUSCHALE_BASISZINS_PLATZHALTER,
    VORABPAUSCHALE_FAKTOR,
    Strategy,
)

# Status-Werte in fetch_log.csv, bei denen der Kurs NICHT frisch abgerufen wurde
# (siehe history_store.record_week) - Grundlage fuer die "eingefroren"-Markierung (#42).
_STALE_STATUS = {"carried_forward", "missing"}

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "index.html"

# Anzeigenamen der vier Optimierungs-Schalter (siehe strategies.Optimierungen / #17).
_OPTIMIERUNGS_LABELS: dict[str, str] = {
    "steueroptimierung": "Steueroptimierung (Dezember-Harvest)",
    "rebalancing": "Rebalancing",
    "ordergebuehren": "Ordergebühren",
    "besteuerung": "Besteuerung",
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
# Bewusster Platzhalter (analog VORABPAUSCHALE_BASISZINS_PLATZHALTER in
# strategies.py): ein echter risikofreier Zins (z. B. laufzeitnahe
# Bundesanleihen-Rendite) ist hier noch nicht hinterlegt, 0% ist eine
# vereinfachende Annahme.
_RISIKOFREIER_ZINS_PLATZHALTER = 0.0


def _sharpe_ratio(total_values: list[float]) -> float:
    """Annualisierte Ueberrendite je Einheit annualisierter Volatilitaet
    (Standardabweichung aller Wochenrenditen, positive wie negative)."""
    renditen = _wochenrenditen(total_values)
    if len(renditen) < 2:
        return 0.0
    std_pct = statistics.pstdev(renditen) * (52**0.5)
    if std_pct == 0:
        return 0.0
    ann_mean_pct = statistics.fmean(renditen) * 52
    return (ann_mean_pct - _RISIKOFREIER_ZINS_PLATZHALTER) / std_pct


def _downside_deviation(renditen: list[float], ziel: float = 0.0) -> float:
    """Wurzel des mittleren quadrierten Unterschreitens von ``ziel`` ueber ALLE
    Wochen (nicht nur die negativen) - Standarddefinition der Sortino-Kennzahl."""
    if not renditen:
        return 0.0
    quadrate = [min(r - ziel, 0.0) ** 2 for r in renditen]
    return (sum(quadrate) / len(quadrate)) ** 0.5


def _sortino_ratio(total_values: list[float]) -> float:
    """Wie ``_sharpe_ratio``, aber nur Verlustwochen fliessen ins Risikomass ein -
    Streuung nach oben (Gewinnwochen) wird nicht als Risiko gewertet."""
    renditen = _wochenrenditen(total_values)
    if len(renditen) < 2:
        return 0.0
    downside_pct = _downside_deviation(renditen) * (52**0.5)
    if downside_pct == 0:
        return 0.0
    ann_mean_pct = statistics.fmean(renditen) * 52
    return (ann_mean_pct - _RISIKOFREIER_ZINS_PLATZHALTER) / downside_pct


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


def _jahre_zurueck(stichtag: date, jahre: int) -> date:
    """``stichtag`` minus ``jahre`` volle Jahre - faellt bei einem nicht
    existierenden 29. Februar auf den 28. zurueck, statt eine Exception zu
    werfen."""
    try:
        return stichtag.replace(year=stichtag.year - jahre)
    except ValueError:
        return stichtag.replace(year=stichtag.year - jahre, day=28)


def _zeitraum_presets(rows: list[PriceRow], strategy: Strategy) -> list[dict]:
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
        result = simulate(preset_rows, strategy)
        total_values = [_f(vp.total_value) for vp in result.value_history]
        labels = [vp.date.isoformat() for vp in result.value_history]
        rendite_pct = _rendite_pct(result, strategy)
        presets.append(
            {
                "id": preset_id,
                "label": _ZEITRAUM_PRESET_LABELS[preset_id],
                "rendite_pct": _f(rendite_pct),
                "rendite_label": f"{rendite_pct:+.2f}",
                "volatilitaet_label": f"{_volatilitaet_pct(total_values):.2f}",
                "max_drawdown_label": f"{-_max_drawdown_pct(total_values):.2f}",
                "sharpe_label": f"{_sharpe_ratio(total_values):.2f}",
                "sortino_label": f"{_sortino_ratio(total_values):.2f}",
                "labels": labels,
                "total_values": total_values,
                "chart_max": max(total_values) if total_values else 0.0,
            }
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


def _optimierungs_effekte(
    strategy: Strategy, rows: list[PriceRow], basis_cagr_pct: float, tage: int
) -> list[dict]:
    """Effekt jedes der vier Optimierungs-Schalter (#17) als Leave-one-out-Differenz
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
    volatilitaet_pct = _volatilitaet_pct(total_values)
    max_drawdown_pct = _max_drawdown_pct(total_values)
    sharpe_ratio = _sharpe_ratio(total_values)
    sortino_ratio = _sortino_ratio(total_values)
    cash_max_pct, cash_max_datum = _cash_anteil_max(points)
    walk_forward_segmente = _walk_forward_segmente(rows, strategy)
    walk_forward_spread_pp = (
        max(seg["rendite_pct"] for seg in walk_forward_segmente)
        - min(seg["rendite_pct"] for seg in walk_forward_segmente)
        if walk_forward_segmente
        else 0.0
    )
    zeitraum_presets = _zeitraum_presets(rows, strategy)

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
        "rendite_pct": _f(rendite_pct),
        "rendite_pct_label": f"{rendite_pct:+.2f}",
        "cagr_pct": cagr_pct,
        "cagr_label": f"{cagr_pct:+.2f}",
        "netto_rendite_pct_label": f"{netto_rendite_pct:+.2f}",
        "netto_cagr_label": f"{netto_cagr_pct:+.2f}",
        "gewinn_label": f"{gewinn:+.2f}",
        "volatilitaet_pct": volatilitaet_pct,
        "volatilitaet_label": f"{volatilitaet_pct:.2f}",
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_label": f"{-max_drawdown_pct:.2f}" if max_drawdown_pct else "0.00",
        "sharpe_ratio": sharpe_ratio,
        "sharpe_label": f"{sharpe_ratio:.2f}",
        "sortino_ratio": sortino_ratio,
        "sortino_label": f"{sortino_ratio:.2f}",
        "cash_max_pct": cash_max_pct,
        "cash_max_label": f"{cash_max_pct:.1f}",
        "cash_max_datum": cash_max_datum or "–",
        "sim_beginn": points[0].date.isoformat(),
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
        # Eigene Y-Achsen-Skalierung statt des gemeinsamen Chart-Maximums (siehe
        # Strategy.eigene_chart_skala) - own_chart_max ist dabei bewusst NUR das
        # Maximum der eigenen Wertreihe, unabhaengig von allen anderen Strategien.
        "eigene_chart_skala": strategy.eigene_chart_skala,
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
            "ausschuettend": "ja" if inst.ausschuettend else "nein",
            "spekulationsfrist": (
                f"{inst.spekulationsfrist_tage} Tage" if inst.spekulationsfrist_tage else "–"
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
                "ziel_gewicht": f"{s.ziel_gewicht * 100:.0f}",
                "dynamisch": "ja" if s.gewichte_fn is not None else "nein",
                "toepfe": [
                    {"name": t.name, "gewicht": f"{t.gewicht_gesamt * 100:.0f}"} for t in s.toepfe
                ],
                "cash_max_label": view.get("cash_max_label", "0.0"),
                "cash_max_datum": view.get("cash_max_datum", "–"),
                "sim_beginn": view.get("sim_beginn", "–"),
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
        "risikofreier_zins": f"{_RISIKOFREIER_ZINS_PLATZHALTER * 100:.0f}",
        "sma_kurz": _SMA_KURZ_WOCHEN,
        "sma_lang": _SMA_LANG_WOCHEN,
        "walk_forward_segmente": _WALK_FORWARD_SEGMENTE,
        "walk_forward_min_wochen": _WALK_FORWARD_MIN_WOCHEN_PRO_SEGMENT,
        "cash_ueberall_null": cash_ueberall_null,
        "btc_fruehphase_ende": _BTC_FRUEHPHASE_ENDE.isoformat(),
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
        _build_strategy_view(s, simulate(strategie_rows[s.name], s), strategie_rows[s.name], carry_forward)
        for s in strategies
    ]
    # Sortierung nach CAGR statt Gesamtrendite (F6b, #63): CAGR ist die Leitkennzahl
    # der Uebersichtstabelle, die Reihenfolge soll dazu konsistent sein.
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

    common_context = dict(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        row_count=len(price_history),
        last_date=price_history[-1].date.isoformat(),
        # #66: aus den tatsächlich allokierten Tickern abgeleitet statt hart
        # eingetragen, damit die Zahl auf jeder Seite automatisch mit einem
        # künftigen Instrumente-/Strategiewechsel mitzieht.
        instrumente_anzahl=len(_allokierte_ticker(strategies)),
    )

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    index_template = env.get_template("dashboard.html.j2")
    index_html = index_template.render(
        strategies=views,
        summary=summary,
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

    praemissen_html = env.get_template("praemissen.html.j2").render(
        **_praemissen_kontext(rows, strategies, views), **common_context
    )
    (output_path.parent / "praemissen.html").write_text(praemissen_html, encoding="utf-8")

    return output_path
