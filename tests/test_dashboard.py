"""Tests für die Dashboard-Rendering-Schicht (``dashboard.py``).

Prüft insbesondere die Vergleichsübersicht (Rendite je Strategie/Szenario) auf
der Startseite und die Verlinkung zu den je Strategie/Szenario erzeugten
Detailseiten (#31) - reine Darstellungslogik, keine eigene Berechnung (die
kommt aus ``engine.simulate()``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from boersenspiel.dashboard import _max_drawdown_pct, _slug, _volatilitaet_pct, build_dashboard
from boersenspiel.history_store import FetchLogEntry, PriceRow
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


def _detail_html(tmp_path: Path, slug: str) -> str:
    return (tmp_path / f"{slug}.html").read_text(encoding="utf-8")


def test_slug_transliterates_umlaute_and_strips_special_chars():
    assert _slug("Börsenweisheit: Sell in May") == "boersenweisheit-sell-in-may"
    assert _slug("Charttechnik: SMA-Crossover (10/40 Wochen)") == "charttechnik-sma-crossover-10-40-wochen"


def test_build_dashboard_renders_comparison_overview_with_correct_ranking(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Übersicht: Rendite im Vergleich" in html
    # Startseite verlinkt auf die je Strategie erzeugten Detailseiten statt auf
    # Anker innerhalb derselben Seite (#31).
    assert 'href="a-verdoppler.html"' in html
    assert 'href="b-verlierer.html"' in html

    # Beide Strategien kaufen dasselbe Instrument T1 zum selben Startkapital
    # (kein Rebalancing, Schwelle ist unerreichbar hoch) -> identisches Ergebnis:
    # 999 investierbar (1000 - 1 Gebuehr) / 100 = 9.99 Einheiten, Endwert bei
    # Kurs 150 = 1498.50, Rendite (1498.50-1000)/1000 = +49.85%.
    uebersicht = html.split('id="uebersicht"', 1)[1].split("</section>", 1)[0]
    assert uebersicht.count("+49.85") == 2


def test_build_dashboard_erzeugt_detailseite_je_strategie(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")

    assert (tmp_path / "a-verdoppler.html").exists()
    assert (tmp_path / "b-verlierer.html").exists()
    detail = _detail_html(tmp_path, "a-verdoppler")
    assert "A: Verdoppler" in detail
    assert '<a href="index.html">' in detail  # Ruecklink zur Startseite


def test_startseite_zeigt_nur_wertverlauf_alles_andere_auf_detailseite(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    index_html = output.read_text(encoding="utf-8")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    # Wertverlauf-Chart bleibt auf der Startseite (#31).
    assert 'id="value-chart-1"' in index_html
    assert "Wertverlauf" in index_html
    # Stat-Kacheln, Topf-Gewichtung und Instrumententabelle sind Detailseiten-Inhalt,
    # nicht (mehr) auf der Startseite.
    assert '<div class="label">Gesamtwert</div>' not in index_html
    assert "Topf-Gewichtung Ist vs. Ziel" not in index_html
    assert "Ist-Gewicht %" not in index_html

    assert '<div class="label">Gesamtwert</div>' in detail_html
    assert "Topf-Gewichtung Ist vs. Ziel" in detail_html
    assert "Ist-Gewicht %" in detail_html
    # 50-/200-Tage-Naeherung (#31) nur auf der Detailseite.
    assert "50-Tage-Näherung" in detail_html
    assert "200-Tage-Näherung" in detail_html
    assert "50-Tage-Näherung" not in index_html


def test_build_dashboard_summary_matches_detail_total_value(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    index_html = output.read_text(encoding="utf-8")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "1498.50" in index_html
    assert "1498.50" in detail_html


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
    build_dashboard(_zwei_ticker_rows(), [_strategy_mit_beitraegen()], output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "kombiniert")

    assert "Effekt der einzelnen Börsenweisheiten" in detail_html
    assert 'id="beitrag-chart"' in detail_html
    assert "Regel A" in detail_html
    assert "Regel B" in detail_html
    # Identische Varianten -> Leave-one-out-Differenz exakt 0.
    assert detail_html.count("+0.00") >= 2


def test_build_dashboard_omits_beitrag_section_without_beitraege(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "Effekt der einzelnen Börsenweisheiten" not in detail_html
    assert "beitrag-chart" not in detail_html


# --- Risikokennzahlen: Volatilitaet & Max Drawdown (#40) ------------------------------


def _auf_und_ab_rows() -> list[PriceRow]:
    """T1 verdoppelt sich, faellt dann auf den Ausgangswert zurueck und erholt sich
    teilweise - erzeugt sowohl einen messbaren Drawdown als auch Streuung in den
    Wochenrenditen (anders als die monoton steigende Standard-Fixture ``_rows()``)."""
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("200")}),
        PriceRow(date(2024, 1, 15), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 22), {"T1": Decimal("150")}),
    ]


def test_volatilitaet_pct_von_konstanter_reihe_ist_null():
    assert _volatilitaet_pct([1000.0, 1000.0, 1000.0]) == 0.0


def test_volatilitaet_pct_erkennt_streuung():
    # Wechselnde Vorzeichen bei den Wochenrenditen (+100%, -50%, +50%) -> deutlich
    # von 0 verschiedene Standardabweichung.
    assert _volatilitaet_pct([100.0, 200.0, 100.0, 150.0]) > 0.0


def test_max_drawdown_pct_bei_monotonem_anstieg_ist_null():
    assert _max_drawdown_pct([100.0, 150.0, 200.0]) == 0.0


def test_max_drawdown_pct_misst_groessten_ruecksetzer():
    # Hoechststand 200, danach Rueckgang auf 100 -> 50% Drawdown; die anschliessende
    # Teilerholung auf 150 bleibt darunter und darf das Maximum nicht verkleinern.
    assert _max_drawdown_pct([100.0, 200.0, 100.0, 150.0]) == 50.0


def test_summary_table_zeigt_volatilitaet_und_max_drawdown_spalten(tmp_path: Path):
    output = build_dashboard(_auf_und_ab_rows(), [ZWEI_STRATEGIEN[0]], output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Volatilität % (ann.)" in html
    assert "Max Drawdown %" in html


def test_risikokennzahlen_sind_nur_auf_startseite(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")
    # Volatilitaet/Max Drawdown sind Teil der Vergleichsuebersicht (#40), nicht der
    # Detailseite - dieselbe Trennung wie bei Wertverlauf-Chart vs. Kennzahl-Kacheln.
    assert "Max Drawdown %" not in detail_html


# --- Klumpenrisiko-Warnung im Rebalancing (#41) ----------------------------------------


def _konzentrations_strategie() -> Strategy:
    return Strategy(
        name="Konzentriert",
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
        rebalancing_schwelle_pp=Decimal("10"),
    )


def _drift_rows() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("1000"), "T2": Decimal("100")}),
    ]


def test_holdings_table_warnt_bei_instrument_konzentration_trotz_topf_im_ziel(tmp_path: Path):
    # Einziger Topf haelt IMMER 100% des Depots -> der Topf-A-Rebalancing-Trigger
    # (#41) loest nie aus, obwohl T1 hier den Grossteil des Topfs uebernimmt.
    build_dashboard(_drift_rows(), [_konzentrations_strategie()], output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "konzentriert")

    assert "Abw. pp" in detail_html
    assert "&#9888;" in detail_html  # Warnsymbol fuer die zu stark abweichenden Instrumente


def test_holdings_table_keine_warnung_wenn_abweichung_unter_schwelle(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")
    # Einziges Instrument haelt exakt sein Zielgewicht (100%) -> keine Abweichung.
    assert "&#9888;" not in detail_html


# --- Sichtbarkeit eingefrorener Kurse (#42) --------------------------------------------


def test_holdings_table_markiert_eingefrorenen_kurs(tmp_path: Path):
    fetch_log = [
        FetchLogEntry(
            date=date(2024, 1, 8),
            ticker="T1",
            status="carried_forward",
            source="test",
            note="Kein aktueller Kurs verfuegbar, letzter bekannter Kurs uebernommen",
        )
    ]
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html", fetch_log=fetch_log)
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "seit 1 Woche eingefroren" in detail_html


def test_holdings_table_ohne_fetch_log_zeigt_keine_eingefroren_markierung(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "eingefroren" not in detail_html
