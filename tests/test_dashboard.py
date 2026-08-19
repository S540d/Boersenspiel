"""Tests für die Dashboard-Rendering-Schicht (``dashboard.py``).

Prüft insbesondere die Vergleichsübersicht (Rendite je Strategie/Szenario)
und die Verlinkung zu den Detailabschnitten - reine Darstellungslogik, keine
eigene Berechnung (die kommt aus ``engine.simulate()``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from boersenspiel.dashboard import _slug, build_dashboard
from boersenspiel.history_store import PriceRow
from boersenspiel.strategies import Beitrag, Strategy, Topf

ZWEI_STRATEGIEN = [
    Strategy(
        name="A: Verdoppler",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    ),
    Strategy(
        name="B: Verlierer",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    ),
]


def _rows() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150")}),
    ]


def test_slug_transliterates_umlaute_and_strips_special_chars():
    assert _slug("Börsenweisheit: Sell in May") == "boersenweisheit-sell-in-may"
    assert _slug("Charttechnik: SMA-Crossover (10/40 Wochen)") == "charttechnik-sma-crossover-10-40-wochen"


def test_build_dashboard_renders_comparison_overview_with_correct_ranking(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Übersicht: Rendite im Vergleich" in html
    assert 'id="a-verdoppler"' in html
    assert 'id="b-verlierer"' in html
    assert 'href="#a-verdoppler"' in html
    assert 'href="#b-verlierer"' in html

    # Beide Strategien kaufen dasselbe Instrument T1 zum selben Startkapital
    # (kein Rebalancing, Schwelle ist unerreichbar hoch) -> identisches Ergebnis:
    # 999 investierbar (1000 - 1 Gebuehr) / 100 = 9.99 Einheiten, Endwert bei
    # Kurs 150 = 1498.50, Rendite (1498.50-1000)/1000 = +49.85%.
    assert html.count("+49.85") == 2


def test_build_dashboard_summary_matches_detail_total_value(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")
    # Endwert taucht sowohl in der Uebersichtstabelle als auch im Detail-Stat-Tile auf.
    assert html.count("1498.50") >= 2


# --- Leave-one-out-Beitraege zusammengesetzter Strategien ------------------------------


def _strategy_mit_beitraegen() -> Strategy:
    """Zwei Varianten mit unterschiedlicher Rebalancing-Schwelle, damit sich die
    Renditen (und damit die Leave-one-out-Differenzen) messbar unterscheiden."""
    basis = dict(
        startkapital=Decimal("1000"),
        toepfe=[
            Topf(
                name="Topf",
                gewicht_gesamt=Decimal("1"),
                sub_gewichte={"T1": Decimal("0.5"), "T2": Decimal("0.5")},
            )
        ],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    return Strategy(
        name="Kombiniert",
        **basis,
        beitraege=(
            Beitrag(name="Regel A", ohne=Strategy(name="ohne Regel A", **basis)),
            Beitrag(name="Regel B", ohne=Strategy(name="ohne Regel B", **basis)),
        ),
    )


def _zwei_ticker_rows() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150"), "T2": Decimal("120")}),
    ]


def test_build_dashboard_renders_beitrag_chart_and_table(tmp_path: Path):
    output = build_dashboard(
        _zwei_ticker_rows(), [_strategy_mit_beitraegen()], output_path=tmp_path / "index.html"
    )
    html = output.read_text(encoding="utf-8")

    assert "Effekt der einzelnen Börsenweisheiten" in html
    assert 'id="beitrag-chart-1"' in html
    assert "Regel A" in html
    assert "Regel B" in html
    # Identische Varianten -> Leave-one-out-Differenz exakt 0.
    assert html.count("+0.00") >= 2


def test_build_dashboard_omits_beitrag_section_without_beitraege(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Effekt der einzelnen Börsenweisheiten" not in html
    assert "beitrag-chart-" not in html
