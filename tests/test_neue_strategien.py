"""Tests für die beiden in #64 ergänzten Strategien.

``BARBELL_20_80_DIVERSIFIZIERT`` nutzt sechs der sieben in #64 aufgenommenen
Datenreihen (XEON, EXSA, IBCL, IBCI, IQQ6, EXXY) für ein breiter gestreutes
Barbell-Portfolio; ``SP500_BENCHMARK`` nutzt die siebte (IUSA) als reine
Vergleichslinie ("einfach den Index kaufen"). Beide sind zusätzliche
Strategien neben den bestehenden - Regressionsschutz, dass Sub-Gewichte zu 1
summieren und die Simulation End-to-End durchläuft.

Zusätzlich: Regressionstest für die im Zuge von #64 korrigierten
Steuerattribute von IBCI/EXXY (tatsächlich thesaurierende Acc-Anteilsklassen,
vorher fälschlich als ausschüttend markiert).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.instruments import INSTRUMENTS
from boersenspiel.strategies import (
    BARBELL_20_80_DIVERSIFIZIERT,
    SP500_BENCHMARK,
    STRATEGIES,
)


def test_neue_strategien_sind_in_strategies_list_registriert():
    assert BARBELL_20_80_DIVERSIFIZIERT in STRATEGIES
    assert SP500_BENCHMARK in STRATEGIES


def test_diversifiziert_ticker_gewichte_summieren_zu_eins():
    gewichte = BARBELL_20_80_DIVERSIFIZIERT.alle_ticker_gewichte()
    assert sum(gewichte.values()) == Decimal("1")


def test_diversifiziert_haelt_barbell_risikoprofil_20_80():
    sicherheit_topf = BARBELL_20_80_DIVERSIFIZIERT.toepfe[0]
    wachstum_topf = BARBELL_20_80_DIVERSIFIZIERT.toepfe[1]
    assert sicherheit_topf.gewicht_gesamt == Decimal("0.20")
    assert wachstum_topf.gewicht_gesamt == Decimal("0.80")
    assert sum(sicherheit_topf.sub_gewichte.values()) == Decimal("1")
    assert sum(wachstum_topf.sub_gewichte.values()) == Decimal("1")


def test_diversifiziert_topf_a_hat_echten_cash_baustein_statt_aktien_etf():
    sicherheit_topf = BARBELL_20_80_DIVERSIFIZIERT.toepfe[0]
    assert "XEON" in sicherheit_topf.sub_gewichte
    assert "EUNL" not in sicherheit_topf.sub_gewichte


def test_diversifiziert_nutzt_sechs_der_sieben_neuen_instrumente():
    gewichte = BARBELL_20_80_DIVERSIFIZIERT.alle_ticker_gewichte()
    for ticker in ["XEON", "EXSA", "IBCL", "IBCI", "IQQ6", "EXXY"]:
        assert ticker in gewichte
    assert "IUSA" not in gewichte


def test_benchmark_haelt_ausschliesslich_iusa():
    gewichte = SP500_BENCHMARK.alle_ticker_gewichte()
    assert gewichte == {"IUSA": Decimal("1")}


def test_benchmark_rebalanciert_nie():
    assert SP500_BENCHMARK.optimierungen.rebalancing is False


def _rows_fuer_diversifiziert() -> list[PriceRow]:
    ticker_kurse_1 = {
        "EUNA": Decimal("5"),
        "4GLD": Decimal("60"),
        "XEON": Decimal("100"),
        "IBCL": Decimal("150"),
        "IBCI": Decimal("200"),
        "EUNL": Decimal("80"),
        "EXSA": Decimal("50"),
        "LYMS": Decimal("20"),
        "SEMI": Decimal("45"),
        "EIMI": Decimal("30"),
        "IQQ6": Decimal("25"),
        "EXXY": Decimal("35"),
        "BTC-EUR": Decimal("40000"),
    }
    ticker_kurse_2 = {t: v * Decimal("1.1") for t, v in ticker_kurse_1.items()}
    return [
        PriceRow(date(2024, 1, 1), dict(ticker_kurse_1)),
        PriceRow(date(2024, 6, 1), ticker_kurse_2),
    ]


def test_diversifiziert_runs_end_to_end():
    result = simulate(_rows_fuer_diversifiziert(), BARBELL_20_80_DIVERSIFIZIERT)
    assert result.strategy_name == "Barbell 20/80 (breiter diversifiziert)"
    assert result.value_history[-1].total_value > 0


def test_benchmark_runs_end_to_end():
    rows = [
        PriceRow(date(2024, 1, 1), {"IUSA": Decimal("60")}),
        PriceRow(date(2024, 6, 1), {"IUSA": Decimal("66")}),
    ]
    result = simulate(rows, SP500_BENCHMARK)
    assert result.strategy_name == "Benchmark: S&P 500 (Buy & Hold)"
    assert result.value_history[-1].total_value > 0


def test_ibci_und_exxy_sind_tatsaechlich_thesaurierend_nicht_ausschuettend():
    assert INSTRUMENTS["IBCI"].thesaurierend is True
    assert INSTRUMENTS["IBCI"].ausschuettend is False
    assert INSTRUMENTS["EXXY"].thesaurierend is True
    assert INSTRUMENTS["EXXY"].ausschuettend is False
