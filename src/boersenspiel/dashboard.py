"""Rendert Simulationsergebnisse als statisches HTML-Dashboard (Chart.js, CDN).

Läuft standardmäßig gegen ALLE in ``strategies.py`` hinterlegten Strategien
und zeigt sie nebeneinander, damit unterschiedliche Strategien direkt
verglichen werden können. Reine Darstellungsschicht ohne eigene
Berechnungslogik - alle Zahlen kommen aus ``engine.simulate()``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .engine import SimulationResult, simulate
from .history_store import PriceRow
from .learnings import derive_learnings
from .strategies import STRATEGIES, Strategy

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "index.html"


def _f(value: Decimal) -> float:
    return float(value)


_UMLAUT_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _slug(name: str) -> str:
    normalisiert = name.lower().translate(_UMLAUT_TRANSLIT)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalisiert)).strip("-")


def _rendite_pct(result: SimulationResult, strategy: Strategy) -> Decimal:
    if strategy.startkapital <= 0:
        return Decimal(0)
    endwert = result.value_history[-1].total_value
    return ((endwert - strategy.startkapital) / strategy.startkapital) * 100


def _build_strategy_view(strategy: Strategy, result: SimulationResult, rows: list[PriceRow]) -> dict:
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
    last = points[-1]
    holdings_table = []
    for ticker, units in sorted(result.holdings.items()):
        value = last.ticker_values.get(ticker, Decimal(0))
        price = (value / units) if units else Decimal(0)
        ist_gewicht = last.ticker_weights.get(ticker, Decimal(0)) * 100
        ziel_gewicht = ticker_targets.get(ticker, Decimal(0)) * 100
        holdings_table.append(
            {
                "ticker": ticker,
                "units": f"{units:.4f}",
                "price": f"{price:.2f}",
                "value": f"{value:.2f}",
                "ist_gewicht": f"{ist_gewicht:.1f}",
                "ziel_gewicht": f"{ziel_gewicht:.1f}",
            }
        )

    gewinn = last.total_value - strategy.startkapital
    rendite_pct = _rendite_pct(result, strategy)

    # Leave-one-out: Einzeleffekt jeder Teilregel als Differenz zur Variante ohne sie.
    beitraege = []
    for beitrag in strategy.beitraege:
        ohne_rendite_pct = _rendite_pct(simulate(rows, beitrag.ohne), beitrag.ohne)
        delta_pp = rendite_pct - ohne_rendite_pct
        beitraege.append(
            {
                "name": beitrag.name,
                "delta_pp": _f(delta_pp),
                "delta_label": f"{delta_pp:+.2f}",
                "ohne_rendite_label": f"{ohne_rendite_pct:+.2f}",
            }
        )

    return {
        "name": result.strategy_name,
        "id": _slug(result.strategy_name),
        "rendite_pct": _f(rendite_pct),
        "rendite_pct_label": f"{rendite_pct:+.2f}",
        "gewinn_label": f"{gewinn:+.2f}",
        "labels_json": json.dumps(labels),
        "total_values_json": json.dumps(total_values),
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
    }


def build_dashboard(
    price_history: list[PriceRow],
    strategies: list[Strategy] | None = None,
    output_path: Path = DEFAULT_OUTPUT,
) -> Path:
    if not price_history:
        raise ValueError("Kurshistorie ist leer - kein Dashboard erzeugbar")
    strategies = strategies if strategies is not None else STRATEGIES
    rows = sorted(price_history, key=lambda r: r.date)

    views = [_build_strategy_view(s, simulate(rows, s), rows) for s in strategies]
    summary = sorted(views, key=lambda v: v["rendite_pct"], reverse=True)
    learnings = derive_learnings(views)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dashboard.html.j2")
    html = template.render(
        strategies=views,
        summary=summary,
        learnings=learnings,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        row_count=len(price_history),
        last_date=price_history[-1].date.isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
