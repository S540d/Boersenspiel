"""Tests für die Dashboard-Rendering-Schicht (``dashboard.py``).

Prüft insbesondere die Vergleichsübersicht (Rendite je Strategie/Szenario) auf
der Startseite und die Verlinkung zu den je Strategie/Szenario erzeugten
Detailseiten (#31) - reine Darstellungslogik, keine eigene Berechnung (die
kommt aus ``engine.simulate()``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import boersenspiel.dashboard as dashboard_module
from boersenspiel.dashboard import (
    _allokierte_ticker,
    _benchmark_reihen,
    _BTC_FRUEHPHASE_ENDE,
    _BTC_TICKER,
    _build_strategy_view,
    _cagr_pct,
    _gemeinsamer_beginn,
    _downside_deviation,
    _jahre_zurueck,
    _VERGLEICH_MIN_WOCHEN,
    _vergleichs_cagr_pct,
    _max_drawdown_pct,
    _ohne_btc_fruehphase,
    _real_investierbarer_zeitraum,
    _sharpe_ratio,
    _slug,
    _sortino_ratio,
    _volatilitaet_pct,
    _walk_forward_segmente,
    _zeitraum_presets,
    build_dashboard,
)
from boersenspiel.engine import simulate
from boersenspiel.history_store import FetchLogEntry, PriceRow
from boersenspiel.instruments import TICKERS
from boersenspiel.strategies import (
    ORDERGEBUEHR,
    SPARERPAUSCHBETRAG_PRO_JAHR,
    Beitrag,
    Optimierungen,
    Strategy,
    Topf,
)

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


# --- Unterszenario-Gruppen-Chart (Strategy.teil_von, #30) ------------------------------


def _strategien_mit_unterszenarien() -> list[Strategy]:
    """Eine Kombi-Strategie plus zwei 'Unterszenarien', die per ``teil_von`` auf
    sie verweisen - analog zu den fünf Börsenweisheiten unter der kombinierten
    Strategie in scenarios.py, aber mit einer trivialen Zwei-Strategien-
    Fixture statt der echten Barbell-Instrumente."""
    basis = dict(
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    return [
        Strategy(name="Kombi", **basis),
        Strategy(name="Kind A", teil_von="Kombi", **basis),
        Strategy(name="Kind B", teil_von="Kombi", **basis),
    ]


def test_build_dashboard_gruppiert_unterszenarien_in_eigenem_chart(tmp_path: Path):
    output = build_dashboard(_rows(), _strategien_mit_unterszenarien(), output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Kombi im Vergleich" in html
    assert 'id="gruppen-chart-1"' in html
    gruppen_chart_js = html.split("getElementById('gruppen-chart-1')", 1)[1].split("options:", 1)[0]
    assert gruppen_chart_js.count("label:") == 3  # Kombi + Kind A + Kind B


def test_build_dashboard_ohne_teil_von_zeigt_keine_gruppen_charts(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Kombi im Vergleich" not in html
    assert "gruppen-chart" not in html


# --- Eigene Chart-Skala fuer weit abweichende Strategien (SP500_BENCHMARK, s. #64) ----


def _strategien_mit_ausreisser() -> list[Strategy]:
    """Eine normale Strategie (T1 verdoppelt sich) plus ein Ausreisser mit
    eigene_chart_skala=True, dessen Wertreihe (T2 verhundertfacht sich) um ein
    Vielfaches groesser ist - reproduziert das Verhaeltnis SP500_BENCHMARK vs.
    die uebrigen Strategien auf der Startseite."""
    basis = dict(
        startkapital=Decimal("1000"),
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    normal = Strategy(
        name="A: Normal",
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        **basis,
    )
    ausreisser = Strategy(
        name="B: Ausreisser",
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T2": Decimal("1")})],
        eigene_chart_skala=True,
        **basis,
    )
    return [normal, ausreisser]


def _rows_mit_ausreisser() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150"), "T2": Decimal("10000")}),
    ]


def test_ausreisser_strategie_fliesst_nicht_ins_gemeinsame_chart_maximum_ein(tmp_path: Path):
    output = build_dashboard(_rows_mit_ausreisser(), _strategien_mit_ausreisser(), output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    # Das gemeinsame Maximum (wertChartMax) darf nur aus "A: Normal" stammen (Endwert
    # rund 1498.50) - der Ausreisser (Endwert rund 99900) wuerde es sonst dominieren
    # und alle anderen Charts auf der Startseite optisch flachdruecken.
    wert_chart_max_js = html.split("const wertChartMax = ", 1)[1].split(";", 1)[0]
    assert float(wert_chart_max_js) < 2000

    # "A: Normal" nutzt weiterhin das gemeinsame Maximum, "B: Ausreisser" sein eigenes.
    chart_1_js = html.split("getElementById('value-chart-1')", 1)[0].rsplit("{\n", 1)[1]
    assert "const eigeneSkala = false;" in chart_1_js
    chart_2_js = html.split("getElementById('value-chart-2')", 1)[0].rsplit("{\n", 1)[1]
    assert "const eigeneSkala = true;" in chart_2_js


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


def test_risikokennzahlen_ausserhalb_des_zeitraum_abschnitts_nur_auf_startseite(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")
    # Volatilitaet/Max Drawdown sind fuer die GESAMTE Historie Teil der
    # Vergleichsuebersicht (#40), nicht der Detailseite - seit #54 gibt es auf der
    # Detailseite aber den eigenen "Kennzahlen nach Betrachtungszeitraum"-Abschnitt,
    # der dieselben Kennzahlen fuer den jeweils gewaehlten Preset zeigt. Die Zeile
    # kommt deshalb GENAU EINMAL vor (im neuen Zeitraum-Abschnitt), nicht zusaetzlich
    # als statische Kennzahl-Kachel.
    assert detail_html.count("Max Drawdown %") == 1
    assert 'id="zeitraum-max-drawdown"' in detail_html


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


# --- Risikoadjustierte Kennzahlen: Sharpe & Sortino -------------------------------


def test_sharpe_ratio_ist_null_bei_konstanter_reihe():
    # Keine Streuung der Wochenrenditen -> Nenner 0 -> bewusst 0.0 statt Division durch 0.
    assert _sharpe_ratio([1000.0, 1000.0, 1000.0]) == 0.0


def test_sharpe_ratio_positiv_bei_positiver_ueberschussrendite():
    assert _sharpe_ratio([100.0, 150.0, 200.0]) > 0.0


def test_sortino_ratio_ist_null_ohne_verlustwochen():
    # Nur Gewinnwochen -> keine Downside-Deviation -> bewusst 0.0 statt undefiniert.
    assert _sortino_ratio([100.0, 150.0, 200.0]) == 0.0


def test_sortino_ratio_positiv_trotz_verlustwoche_bei_positivem_gesamttrend():
    assert _sortino_ratio([100.0, 200.0, 100.0, 150.0]) > 0.0


def test_downside_deviation_ignoriert_streuung_nach_oben():
    # Nur die Verlustwoche (-0.1) fliesst ein; wie stark die Gewinnwochen streuen, ist
    # fuer die Downside-Deviation irrelevant - anders als bei der (Gesamt-)Volatilitaet.
    assert _downside_deviation([0.1, 0.2, -0.1]) == _downside_deviation([0.5, 0.9, -0.1])


def test_downside_deviation_ohne_verlustwochen_ist_null():
    assert _downside_deviation([0.1, 0.2, 0.05]) == 0.0


def test_summary_table_zeigt_sharpe_und_sortino_spalten(tmp_path: Path):
    output = build_dashboard(_auf_und_ab_rows(), [ZWEI_STRATEGIEN[0]], output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert "Sharpe" in html
    assert "Sortino" in html


def test_sharpe_sortino_ausserhalb_des_zeitraum_abschnitts_nur_auf_startseite(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")
    # Seit #54 zeigt der neue "Kennzahlen nach Betrachtungszeitraum"-Abschnitt auf
    # der Detailseite Sharpe/Sortino fuer den gewaehlten Preset - als einzige
    # Stelle, nicht zusaetzlich als statische Kachel (siehe Test oberhalb).
    assert detail_html.count("Sharpe") == 1
    assert detail_html.count("Sortino") == 1
    assert 'id="zeitraum-sharpe"' in detail_html
    assert 'id="zeitraum-sortino"' in detail_html


# --- Walk-Forward-Robustheit ueber Teilperioden -----------------------------------


def _lange_rows(wochen: int = 33) -> list[PriceRow]:
    """Genug Wochen fuer 3 Walk-Forward-Segmente (>= 10 Wochen/Segment); T1 schwankt
    auf und ab statt monoton zu steigen, damit die Teilperioden unterschiedlich
    ausfallen koennen."""
    start = date(2024, 1, 1)
    rows = []
    preis = Decimal("100")
    for i in range(wochen):
        preis = preis * Decimal("1.05") if i % 3 else preis * Decimal("0.9")
        rows.append(PriceRow(start + timedelta(weeks=i), {"T1": preis}))
    return rows


def test_walk_forward_segmente_leer_bei_zu_wenig_wochen():
    # 10 Wochen reichen nicht fuer 2 Segmente à mindestens 10 Wochen.
    assert _walk_forward_segmente(_lange_rows(10), ZWEI_STRATEGIEN[0]) == []


def test_walk_forward_segmente_teilt_lange_historie_in_drei_perioden():
    segmente = _walk_forward_segmente(_lange_rows(33), ZWEI_STRATEGIEN[0])

    assert len(segmente) == 3
    for segment in segmente:
        assert "–" in segment["label"]
        assert "rendite_label" in segment


def test_build_dashboard_zeigt_walk_forward_abschnitt_bei_genug_historie(tmp_path: Path):
    build_dashboard(_lange_rows(33), [ZWEI_STRATEGIEN[0]], output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "Robustheit über Teilperioden (Walk-Forward)" in detail_html
    assert 'id="walk-chart"' in detail_html


def test_build_dashboard_versteckt_walk_forward_abschnitt_bei_kurzer_historie(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "Robustheit über Teilperioden" not in detail_html
    assert "walk-chart" not in detail_html


# --- Zeitraum-Presets (#54, Variante B) -------------------------------------------


def test_jahre_zurueck_normalfall():
    assert _jahre_zurueck(date(2026, 8, 21), 3) == date(2023, 8, 21)


def test_jahre_zurueck_faellt_bei_schaltjahr_auf_28_februar_zurueck():
    # 2024 ist ein Schaltjahr, 2023 nicht - der 29.02.2024 minus 1 Jahr existiert
    # nicht und faellt auf den 28.02.2023 zurueck statt eine Exception zu werfen.
    assert _jahre_zurueck(date(2024, 2, 29), 1) == date(2023, 2, 28)


def _mehrjaehrige_rows(wochen: int = 312) -> list[PriceRow]:
    """Genug Wochen (>= 6 Jahre) fuer alle vier Zeitraum-Presets (1/3/5 Jahre,
    gesamte Historie); T1 steigt linear, damit sich kuerzere gegenueber laengeren
    Zeitraeumen mit einer klar unterscheidbaren Rendite abgrenzen."""
    start = date(2020, 1, 1)
    return [
        PriceRow(start + timedelta(weeks=i), {"T1": Decimal("100") + Decimal(i)})
        for i in range(wochen)
    ]


def test_zeitraum_presets_enthaelt_alle_presets_bei_genug_historie():
    presets = _zeitraum_presets(_mehrjaehrige_rows(), ZWEI_STRATEGIEN[0])
    assert [p["id"] for p in presets] == ["1j", "3j", "5j", "alle"]


def test_zeitraum_presets_sind_frische_neu_simulationen_mit_unterschiedlicher_rendite():
    rows = _mehrjaehrige_rows()
    presets = _zeitraum_presets(rows, ZWEI_STRATEGIEN[0])
    by_id = {p["id"]: p for p in presets}

    # Kuerzerer Zeitraum auf einer monoton steigenden Kursreihe -> kleinere
    # absolute Kursspanne -> niedrigere Rendite als bei der gesamten Historie.
    assert by_id["1j"]["rendite_pct"] < by_id["alle"]["rendite_pct"]

    # "alle" entspricht exakt einer normalen Simulation ueber die volle Historie.
    voll_ergebnis = simulate(rows, ZWEI_STRATEGIEN[0])
    assert by_id["alle"]["total_values"] == [float(vp.total_value) for vp in voll_ergebnis.value_history]


def test_zeitraum_presets_leer_bei_leerer_historie_ist_unerreichbar_da_build_dashboard_das_verhindert():
    # _zeitraum_presets() selbst geht von mindestens einer Zeile aus (wie
    # engine.simulate()) - build_dashboard() weist eine leere Kurshistorie bereits
    # vorher zurueck, siehe test_history_store.py/test_engine.py fuer den
    # allgemeinen Leerfall.
    presets = _zeitraum_presets(_rows(), ZWEI_STRATEGIEN[0])
    assert len(presets) == 4


def test_build_dashboard_zeigt_zeitraum_abschnitt_auf_detailseite(tmp_path: Path):
    build_dashboard(_mehrjaehrige_rows(), [ZWEI_STRATEGIEN[0]], output_path=tmp_path / "index.html")
    detail_html = _detail_html(tmp_path, "a-verdoppler")

    assert "Kennzahlen nach Betrachtungszeitraum" in detail_html
    assert 'id="detail-zeitraum-switch"' in detail_html
    assert 'id="zeitraum-chart"' in detail_html
    for preset_id in ("1j", "3j", "5j", "alle"):
        assert f'data-preset="{preset_id}"' in detail_html


def test_build_dashboard_zeigt_zeitraum_umschalter_auf_startseite(tmp_path: Path):
    output = build_dashboard(_mehrjaehrige_rows(), [ZWEI_STRATEGIEN[0]], output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert 'id="zeitraum-switch-1"' in html
    assert "initZeitraumSwitch" in html


# --- Praemissen-Seite -------------------------------------------------------------


def test_praemissen_seite_wird_erzeugt_und_ueberall_verlinkt(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")

    praemissen = tmp_path / "praemissen.html"
    assert praemissen.exists()
    # Erreichbar ueber das Drei-Punkt-Menue auf JEDER Seite, nicht nur der Startseite.
    assert "praemissen.html" in output.read_text(encoding="utf-8")
    assert "praemissen.html" in _detail_html(tmp_path, "a-verdoppler")


def test_praemissen_seite_leitet_werte_aus_den_echten_konstanten_ab(tmp_path: Path):
    # Kernanspruch der Seite: nichts hier ist hinterlegter Text, der gegenueber
    # dem Code veralten koennte - die Werte kommen aus strategies.py/instruments.py
    # und der uebergebenen Kurshistorie.
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert f"{ORDERGEBUEHR:.2f}" in html
    assert f"{SPARERPAUSCHBETRAG_PRO_JAHR:.0f}" in html
    # Zeitraum und Zeilenzahl stammen aus der uebergebenen Historie.
    assert "2024-01-01" in html
    assert "2024-01-08" in html
    # Alle Instrumente aus instruments.py sind aufgefuehrt, mit ihrer
    # Teilfreistellung und der BTC-Spekulationsfrist.
    for ticker in TICKERS:
        assert ticker in html
    assert "365 Tage" in html


def test_praemissen_seite_benennt_die_wesentlichen_einschraenkungen(tmp_path: Path):
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    # Einzeltoken statt ganzer Saetze: der Fliesstext im Template ist umbrochen,
    # ein Satzfragment wuerde nur zufaellig matchen.
    assert "Anlageberatung" in html
    assert "Rückschaufehler" in html
    # Die Platzhalter muessen als solche gekennzeichnet sein, nicht als echte Werte.
    assert "Platzhalter" in html
    # Nicht modellierte Effekte
    assert "Dividenden" in html
    assert "Inflation" in html


def test_praemissen_seite_markiert_spaeter_verfuegbare_instrumente(tmp_path: Path):
    # T2 hat in der ersten Zeile keinen Kurs -> muss als "erst ab" markiert sein,
    # damit nachvollziehbar bleibt, dass die fruehen Jahre ein schmaleres
    # Portfolio abbilden.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150"), "T2": Decimal("50")}),
    ]
    build_dashboard(rows, ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert "Instrumente und ab wann es sie gab" in html


# --- Cash-Abschnitt (Begruendung "keine separate Cash-Position" + Live-Werte) ----------


def test_praemissen_seite_zeigt_null_prozent_cash_wenn_immer_alles_investiert_ist(tmp_path: Path):
    # ZWEI_STRATEGIEN kaufen T1 sofort vollstaendig und rebalancieren nie -> zu
    # keinem Zeitpunkt ungenutztes Kapital. Die Seite muss das aus den Views
    # ableiten, nicht behaupten.
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert "Cash und ungenutztes Kapital" in html
    assert "Topf A" in html  # Begruendung "Topf A uebernimmt die Cash-Rolle"
    assert "hält aktuell jede" in html
    assert html.count(">0.0<") >= 2


def test_praemissen_seite_zeigt_cash_hoechststand_wenn_kein_zielinstrument_handelbar(tmp_path: Path):
    # T2 hat in KEINER Zeile einen Kurs -> das Kapital dieses (isolierten)
    # Topfs bleibt vollstaendig geparkt (pending_cash), da handelbare_gewichte()
    # nichts zum Umlegen findet.
    strategie = Strategy(
        name="Nur T2",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T2": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    build_dashboard(_rows(), [strategie], output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert "hält aktuell nicht jede" in html
    assert "100.0" in html
    assert "2024-01-01" in html.split("Cash und ungenutztes Kapital", 1)[1]


# --- F6b/c (#63): CAGR als Leitkennzahl -------------------------------------------


def test_cagr_pct_verdoppelt_sich_ueber_ein_jahr_ergibt_100_prozent():
    # Exakte Dezimalfaelle: 1461 Tage = 4 * 365,25 (exakt in float darstellbar) ->
    # jahre=4. Ein Faktor von 16 (rendite_pct=1500%) verteilt sich gleichmaessig
    # ueber vier Jahre auf 16**(1/4)=2, also CAGR=100%.
    assert abs(_cagr_pct(1500.0, 1461) - 100.0) < 1e-9


def test_cagr_pct_totalverlust_ist_minus_hundert_prozent():
    assert _cagr_pct(-100.0, 365) == -100.0


def test_cagr_pct_ohne_tage_ist_null():
    assert _cagr_pct(50.0, 0) == 0.0


def test_summary_table_sortiert_nach_cagr_und_zeigt_gesamtrendite_daneben(tmp_path: Path):
    output = build_dashboard(_mehrjaehrige_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")
    assert "CAGR % p.a." in html
    assert "Gesamtrendite %" in html


# --- F4 (#63): Rueckschaufehler mit Hebel ------------------------------------------


def test_ohne_btc_fruehphase_entfernt_nur_btc_vor_dem_cutoff():
    rows = [
        PriceRow(
            _BTC_FRUEHPHASE_ENDE - timedelta(weeks=1),
            {_BTC_TICKER: Decimal("1"), "T1": Decimal("100")},
        ),
        PriceRow(
            _BTC_FRUEHPHASE_ENDE,
            {_BTC_TICKER: Decimal("2"), "T1": Decimal("100")},
        ),
    ]
    bereinigt = _ohne_btc_fruehphase(rows)

    assert _BTC_TICKER not in bereinigt[0].prices
    assert bereinigt[0].prices["T1"] == Decimal("100")  # andere Ticker unangetastet
    assert _BTC_TICKER in bereinigt[1].prices  # ab dem Cutoff-Datum wieder handelbar


def test_real_investierbarer_zeitraum_schneidet_auf_vollstaendiges_instrumentenset_zu():
    strategy = Strategy(
        name="Zwei-Instrumente",
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
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),  # T2 fehlt noch
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("50")}),  # ab hier vollstaendig
        PriceRow(date(2024, 1, 15), {"T1": Decimal("110"), "T2": Decimal("55")}),
    ]
    geschnitten = _real_investierbarer_zeitraum(rows, strategy)
    assert [r.date for r in geschnitten] == [date(2024, 1, 8), date(2024, 1, 15)]


def test_real_investierbarer_zeitraum_ohne_treffer_gibt_alle_rows_unveraendert():
    strategy = Strategy(
        name="Fehlt-immer",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T2": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    rows = [PriceRow(date(2024, 1, 1), {"T1": Decimal("100")})]  # T2 nie vorhanden
    assert _real_investierbarer_zeitraum(rows, strategy) == rows


# --- F6a (#63): geschaetzte Nettorendite --------------------------------------------

GROSSER_GEWINN_STRATEGY = Strategy(
    name="Grosser-Gewinn",
    startkapital=Decimal("100000"),
    toepfe=[
        Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T1": Decimal("1")}),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T2": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("1"),
)


def test_netto_rendite_liegt_unter_der_bruttorendite_wenn_steuer_anfaellt():
    # T1 verzehnfacht sich -> das Rebalancing realisiert einen Gewinn weit über
    # dem Sparerpauschbetrag (1000 EUR) -> kumulierte_steuer > 0.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("1000"), "T2": Decimal("100")}),
    ]
    result = simulate(rows, GROSSER_GEWINN_STRATEGY)
    assert result.tax_status.kumulierte_steuer > Decimal("0")

    view = _build_strategy_view(GROSSER_GEWINN_STRATEGY, result, rows)
    brutto = float(view["cagr_label"].replace(",", "."))
    netto = float(view["netto_cagr_label"].replace(",", "."))
    assert netto < brutto


# --- #66: veraltete Instrumentenzahl, unallokierte Instrumente unerklaert ----------


def test_allokierte_ticker_ist_die_vereinigung_ueber_alle_strategien():
    # ZWEI_STRATEGIEN haelt beide nur "T1" - unabhaengig davon, wie viele
    # Ticker instruments.py insgesamt kennt.
    assert _allokierte_ticker(ZWEI_STRATEGIEN) == {"T1"}


def test_build_dashboard_zeigt_dynamische_instrumentenzahl_statt_hartkodierter_17(tmp_path: Path):
    output = build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")
    # ZWEI_STRATEGIEN allokiert genau 1 Ticker ("T1") - die feste "17" aus dem
    # Template ist damit hier nicht mehr korrekt, dynamisch schon.
    assert "denselben 1 Instrumenten" in html
    assert "denselben 17 Instrumenten" not in html


def test_praemissen_seite_trennt_allokierte_von_nicht_allokierten_instrumenten(tmp_path: Path):
    # ZWEI_STRATEGIEN allokiert nur "T1" - alle "echten" TICKERS aus
    # instruments.py landen deshalb in der neuen "Datenreihen ohne
    # Allokation"-Sektion statt in der Haupttabelle.
    build_dashboard(_rows(), ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert "<h3>Datenreihen ohne Allokation</h3>" in html
    vor_abschnitt, nach_abschnitt = html.split("<h3>Datenreihen ohne Allokation</h3>", 1)
    assert "EUNL" not in vor_abschnitt.split("Instrumente und ab wann es sie gab", 1)[1]
    assert "EUNL" in nach_abschnitt


def test_praemissen_seite_versteckt_abschnitt_wenn_alles_allokiert_ist(tmp_path: Path):
    # Eine Strategie, die ALLE TICKERS haelt -> keine unallokierten Instrumente
    # -> der Abschnitt darf gar nicht erst gerendert werden.
    alles_strategy = Strategy(
        name="Alles",
        startkapital=Decimal("1000"),
        toepfe=[
            Topf(
                name="Topf",
                gewicht_gesamt=Decimal("1"),
                sub_gewichte={t: Decimal(1) / Decimal(len(TICKERS)) for t in TICKERS},
            )
        ],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    rows = [PriceRow(date(2024, 1, 1), {t: Decimal("100") for t in TICKERS})]
    build_dashboard(rows, [alles_strategy], output_path=tmp_path / "index.html")
    html = (tmp_path / "praemissen.html").read_text(encoding="utf-8")

    assert "<h3>Datenreihen ohne Allokation</h3>" not in html


# --- Benchmark-Overlay-Schalter (#72) ---------------------------------------------


def _benchmark_fixture(name: str = "Benchmark X", ticker: str = "BX") -> Strategy:
    """Reiner Einzelinstrument-Buy&Hold, wie es BENCHMARK_STRATEGIEN in
    strategies.py verlangt (siehe deren Docstring)."""
    return Strategy(
        name=name,
        startkapital=Decimal("10000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={ticker: Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("5"),
        optimierungen=Optimierungen(rebalancing=False),
    )


def _rows_mit_benchmark_ticker() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "BX": Decimal("50")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150"), "BX": Decimal("55")}),
    ]


def test_benchmark_reihen_simuliert_mit_startkapital_der_angezeigten_strategie(monkeypatch):
    bench = _benchmark_fixture()
    monkeypatch.setattr(dashboard_module, "BENCHMARK_STRATEGIEN", [bench])
    strategie = Strategy(
        name="Eigene Strategie",
        startkapital=Decimal("500"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    rows = _rows_mit_benchmark_ticker()

    overlays = _benchmark_reihen(rows, strategie)

    assert len(overlays) == 1
    assert overlays[0]["id"] == _slug("Benchmark X")
    assert overlays[0]["label"] == "Benchmark X"
    # Gleiche Laenge wie die Kurshistorie (ein Punkt je Zeile), Startwert nahe am
    # Startkapital der ANGEZEIGTEN Strategie (500), nicht dem Default der
    # Benchmark-Fixture (10000).
    assert len(overlays[0]["total_values"]) == len(rows)
    assert 490 < overlays[0]["total_values"][0] < 500


def test_benchmark_reihen_schliesst_sich_selbst_aus(monkeypatch):
    bench = _benchmark_fixture(name="Eigene Strategie", ticker="T1")
    monkeypatch.setattr(dashboard_module, "BENCHMARK_STRATEGIEN", [bench])
    strategie = _benchmark_fixture(name="Eigene Strategie", ticker="T1")

    assert _benchmark_reihen(_rows_mit_benchmark_ticker(), strategie) == []


def test_benchmark_reihen_ohne_kursdaten_wird_nicht_angeboten(monkeypatch):
    bench = _benchmark_fixture(ticker="KEIN_KURS")
    monkeypatch.setattr(dashboard_module, "BENCHMARK_STRATEGIEN", [bench])
    strategie = Strategy(
        name="Eigene Strategie",
        startkapital=Decimal("500"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )

    assert _benchmark_reihen(_rows_mit_benchmark_ticker(), strategie) == []


def test_build_dashboard_rendert_benchmark_schalter_und_feste_skala(tmp_path: Path, monkeypatch):
    bench = _benchmark_fixture()
    monkeypatch.setattr(dashboard_module, "BENCHMARK_STRATEGIEN", [bench])
    strategie = Strategy(
        name="Eigene Strategie",
        startkapital=Decimal("500"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )
    output = build_dashboard(_rows_mit_benchmark_ticker(), [strategie], output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert 'id="benchmark-switch"' in html
    assert f'data-benchmark="{_slug("Benchmark X")}"' in html
    assert '"label": "Benchmark X"' in html
    # #72: fixes `max` statt `suggestedMax` auf dem Wertverlauf-Chart, damit die
    # Skala sich durch die Benchmark-Linie nicht veraendert.
    chart_js = html.split("getElementById('value-chart-1')", 1)[1].split("chart.__benchmarks", 1)[0]
    assert "suggestedMax:" not in chart_js
    assert "max: strategieMax * 1.05" in chart_js

    detail_html = _detail_html(tmp_path, "eigene-strategie")
    assert 'id="benchmark-switch"' in detail_html
    assert f'data-benchmark="{_slug("Benchmark X")}"' in detail_html
    assert "suggestedMax:" not in detail_html


# --- Gemeinsamer Vergleichszeitraum (#73) ------------------------------------


def _lange_reihe(start: date, wochen: int, faktor_pro_woche: float, ticker: str) -> list[PriceRow]:
    kurs = Decimal("100")
    rows = []
    for i in range(wochen):
        rows.append(PriceRow(start + timedelta(weeks=i), {ticker: kurs}))
        kurs = kurs * Decimal(str(faktor_pro_woche))
    return rows


def test_gemeinsamer_beginn_ist_das_spaeteste_startdatum():
    a = [PriceRow(date(2020, 1, 6), {"T1": Decimal("100")})]
    b = [PriceRow(date(2023, 5, 1), {"T2": Decimal("100")})]
    assert _gemeinsamer_beginn({"A": a, "B": b}) == date(2023, 5, 1)
    assert _gemeinsamer_beginn({}) is None
    assert _gemeinsamer_beginn({"A": []}) is None


def test_vergleichs_cagr_nutzt_nur_den_zeitraum_ab_dem_gemeinsamen_beginn():
    # Erst 52 Wochen flach, danach 52 Wochen steigend. Die CAGR ueber die volle
    # Historie muss deutlich unter der CAGR ab dem Anstieg liegen - sonst wuerde
    # der Ausschnitt gar nicht wirken.
    flach = [PriceRow(date(2020, 1, 6) + timedelta(weeks=i), {"T1": Decimal("100")}) for i in range(52)]
    steigend = _lange_reihe(date(2021, 1, 4), 60, 1.01, "T1")
    rows = flach + steigend
    strategy = ZWEI_STRATEGIEN[0]

    voll = _cagr_pct(
        float(simulate(rows, strategy).value_history[-1].total_value / strategy.startkapital - 1) * 100,
        (rows[-1].date - rows[0].date).days,
    )
    ausschnitt = _vergleichs_cagr_pct(strategy, rows, date(2021, 1, 4))

    assert ausschnitt is not None
    assert ausschnitt > voll + 10


def test_vergleichs_cagr_entfaellt_bei_zu_kurzem_gemeinsamem_zeitraum():
    rows = _lange_reihe(date(2020, 1, 6), _VERGLEICH_MIN_WOCHEN + 5, 1.01, "T1")
    zu_kurz = rows[-(_VERGLEICH_MIN_WOCHEN - 1)].date
    assert _vergleichs_cagr_pct(ZWEI_STRATEGIEN[0], rows, zu_kurz) is None
    assert _vergleichs_cagr_pct(ZWEI_STRATEGIEN[0], rows, rows[0].date) is not None


def test_uebersichtstabelle_zeigt_vergleichszeitraum_und_ueberrendite(tmp_path: Path):
    # Zwei Strategien mit unterschiedlichem Simulationsbeginn: "Spaet" haelt ein
    # Instrument, das erst spaeter einen Kurs hat, und beginnt deshalb spaeter.
    # Genau diese Konstellation machte die CAGR-Spalten frueher unvergleichbar.
    spaet = Strategy(
        name="Spaet",
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
    frueh = ZWEI_STRATEGIEN[0]

    rows = []
    kurs = Decimal("100")
    start = date(2020, 1, 6)
    for i in range(120):
        preise = {"T1": kurs}
        if i >= 40:
            preise["T2"] = kurs
        rows.append(PriceRow(start + timedelta(weeks=i), preise))
        kurs = kurs * Decimal("1.005")

    build_dashboard(rows, [frueh, spaet], output_path=tmp_path / "index.html")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    # Der gemeinsame Beginn ist der spaetere der beiden Simulationsbeginne.
    gemeinsam = rows[40].date.isoformat()
    assert gemeinsam in html
    assert "im Vergleichszeitraum" in html
    # Beide eigenen Zeitraeume stehen als eigene Spalte in der Tabelle.
    assert rows[0].date.isoformat() in html
    assert rows[-1].date.isoformat() in html


def test_ohne_benchmark_strategie_entfaellt_die_ueberrendite_spalte(tmp_path: Path):
    # ZWEI_STRATEGIEN enthaelt keine Strategie aus BENCHMARK_STRATEGIEN - dann
    # gibt es keine Referenzlinie und die Ueberrendite bleibt leer, statt gegen
    # eine willkuerlich gewaehlte andere Strategie zu rechnen.
    rows = _lange_reihe(date(2020, 1, 6), 60, 1.005, "T1")
    build_dashboard(rows, ZWEI_STRATEGIEN, output_path=tmp_path / "index.html")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "im Vergleichszeitraum" in html
    assert "Überrendite pp p.a." not in html
