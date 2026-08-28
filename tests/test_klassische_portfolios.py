"""Tests für die zwei verbreiteten klassischen Portfolio-Strategien.

``PORTFOLIO_60_40`` (60% Aktien/40% Anleihen) und ``PERMANENT_PORTFOLIO``
(Harry Browne, je 25% Aktien/lange Anleihen/Gold/Cash) sind zusätzliche
Strategien neben den bestehenden Barbell-Varianten und dem S&P-500-Benchmark
- beide nutzen ausschließlich bereits vorhandene Instrumente (EUNL, EUNA,
IBCL, 4GLD, XEON), kein neuer API-Request nötig. Regressionsschutz, dass
Sub-Gewichte zu 1 summieren und die Simulation End-to-End durchläuft.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.strategies import (
    PERMANENT_PORTFOLIO,
    PORTFOLIO_60_40,
    STRATEGIES,
)


def test_neue_strategien_sind_in_strategies_list_registriert():
    assert PORTFOLIO_60_40 in STRATEGIES
    assert PERMANENT_PORTFOLIO in STRATEGIES


def test_60_40_ticker_gewichte_summieren_zu_eins():
    gewichte = PORTFOLIO_60_40.alle_ticker_gewichte()
    assert sum(gewichte.values()) == Decimal("1")


def test_60_40_haelt_klassisches_risikoprofil():
    aktien_topf = PORTFOLIO_60_40.toepfe[0]
    anleihen_topf = PORTFOLIO_60_40.toepfe[1]
    assert aktien_topf.gewicht_gesamt == Decimal("0.60")
    assert anleihen_topf.gewicht_gesamt == Decimal("0.40")
    assert aktien_topf.sub_gewichte == {"EUNL": Decimal("1")}
    assert anleihen_topf.sub_gewichte == {"EUNA": Decimal("1")}


def test_permanent_portfolio_ticker_gewichte_summieren_zu_eins():
    gewichte = PERMANENT_PORTFOLIO.alle_ticker_gewichte()
    assert sum(gewichte.values()) == Decimal("1")


def test_permanent_portfolio_haelt_vier_gleich_grosse_toepfe():
    assert len(PERMANENT_PORTFOLIO.toepfe) == 4
    for topf in PERMANENT_PORTFOLIO.toepfe:
        assert topf.gewicht_gesamt == Decimal("0.25")
        assert sum(topf.sub_gewichte.values()) == Decimal("1")


def test_permanent_portfolio_nutzt_je_ein_instrument_pro_anlageklasse():
    gewichte = PERMANENT_PORTFOLIO.alle_ticker_gewichte()
    assert gewichte == {
        "EUNL": Decimal("0.25"),
        "IBCL": Decimal("0.25"),
        "4GLD": Decimal("0.25"),
        "XEON": Decimal("0.25"),
    }


def _rows_60_40() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"EUNL": Decimal("80"), "EUNA": Decimal("5")}),
        PriceRow(date(2024, 6, 1), {"EUNL": Decimal("88"), "EUNA": Decimal("5.5")}),
    ]


def test_60_40_runs_end_to_end():
    result = simulate(_rows_60_40(), PORTFOLIO_60_40)
    assert result.strategy_name == "60/40-Portfolio"
    assert result.value_history[-1].total_value > 0


def _rows_permanent_portfolio() -> list[PriceRow]:
    ticker_kurse_1 = {
        "EUNL": Decimal("80"),
        "IBCL": Decimal("150"),
        "4GLD": Decimal("60"),
        "XEON": Decimal("100"),
    }
    ticker_kurse_2 = {t: v * Decimal("1.1") for t, v in ticker_kurse_1.items()}
    return [
        PriceRow(date(2024, 1, 1), dict(ticker_kurse_1)),
        PriceRow(date(2024, 6, 1), ticker_kurse_2),
    ]


def test_permanent_portfolio_runs_end_to_end():
    result = simulate(_rows_permanent_portfolio(), PERMANENT_PORTFOLIO)
    assert result.strategy_name == "Permanent Portfolio"
    assert result.value_history[-1].total_value > 0
