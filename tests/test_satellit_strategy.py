"""Tests für die Erweiterung um 10 volatile Einzelaktien (Einzelaktien-Satellit).

Erster Block: strukturelle Konsistenz von Instrumenten-Universum und
Alpha-Vantage-Symbol-Mapping (Regressionsschutz - jeder neue Ticker in
``instruments.py`` braucht ein Mapping, sonst liefert der Kursabruf
stillschweigend "missing", siehe ``alphavantage._fetch_quote``).

Zweiter Block: die neue Strategie ``BARBELL_20_60_20_SATELLIT`` selbst -
Ziel-Gewichte summieren zu 1, Gesamtrisikoprofil (80% riskant / 20% sicher)
bleibt wie bei Barbell 20/80 erhalten, End-to-End-Smoke-Test mit allen 17
Instrumenten.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.instruments import TICKERS
from boersenspiel.sources.alphavantage import ALPHAVANTAGE_SYMBOLS
from boersenspiel.strategies import BARBELL_20_60_20_SATELLIT, STRATEGIES

EINZELAKTIEN_SATELLIT_TICKER = [
    "LITE",
    "BYDDY",
    "SEDG",
    "S92",
    "TSLA",
    "PLTR",
    "MSTR",
    "RIVN",
    "KO",
    "RHHBY",
]


def test_every_non_crypto_ticker_has_alphavantage_symbol_mapping():
    fehlend = [t for t in TICKERS if t != "BTC-EUR" and t not in ALPHAVANTAGE_SYMBOLS]
    assert fehlend == []


def test_satellit_strategy_is_registered_in_strategies_list():
    assert BARBELL_20_60_20_SATELLIT in STRATEGIES


def test_satellit_strategy_ticker_gewichte_summieren_zu_eins():
    gewichte = BARBELL_20_60_20_SATELLIT.alle_ticker_gewichte()
    assert sum(gewichte.values()) == Decimal("1")


def test_satellit_strategy_haelt_barbell_risikoprofil_80_20():
    gewichte = BARBELL_20_60_20_SATELLIT.alle_ticker_gewichte()
    sicherheit = sum(
        g for t, g in gewichte.items() if t in {"EUNL", "EUNA", "4GLD"}
    )
    riskant = sum(g for t, g in gewichte.items() if t not in {"EUNL", "EUNA", "4GLD"})
    assert sicherheit == Decimal("0.20")
    assert riskant == Decimal("0.80")


def test_satellit_strategy_gewichtet_alle_10_einzelaktien_gleich():
    gewichte = BARBELL_20_60_20_SATELLIT.alle_ticker_gewichte()
    for ticker in EINZELAKTIEN_SATELLIT_TICKER:
        assert gewichte[ticker] == Decimal("0.02")  # 20% Topf * 10% Sub-Gewicht


def test_satellit_strategy_runs_end_to_end_with_all_17_instrumenten():
    rows = [
        PriceRow(
            date(2024, 1, 1),
            {
                "EUNL": Decimal("80"),
                "EUNA": Decimal("5"),
                "4GLD": Decimal("60"),
                "LYMS": Decimal("20"),
                "SEMI": Decimal("45"),
                "EIMI": Decimal("30"),
                "BTC-EUR": Decimal("40000"),
                "LITE": Decimal("70"),
                "BYDDY": Decimal("55"),
                "SEDG": Decimal("15"),
                "S92": Decimal("60"),
                "TSLA": Decimal("250"),
                "PLTR": Decimal("40"),
                "MSTR": Decimal("300"),
                "RIVN": Decimal("12"),
                "KO": Decimal("65"),
                "RHHBY": Decimal("35"),
            },
        ),
        PriceRow(
            date(2024, 6, 1),
            {
                "EUNL": Decimal("90"),
                "EUNA": Decimal("5.2"),
                "4GLD": Decimal("65"),
                "LYMS": Decimal("28"),
                "SEMI": Decimal("55"),
                "EIMI": Decimal("27"),
                "BTC-EUR": Decimal("60000"),
                "LITE": Decimal("90"),
                "BYDDY": Decimal("48"),
                "SEDG": Decimal("22"),
                "S92": Decimal("50"),
                "TSLA": Decimal("310"),
                "PLTR": Decimal("62"),
                "MSTR": Decimal("410"),
                "RIVN": Decimal("9"),
                "KO": Decimal("67"),
                "RHHBY": Decimal("36"),
            },
        ),
    ]
    result = simulate(rows, BARBELL_20_60_20_SATELLIT)

    assert result.strategy_name == "Barbell 20/60/20 + Einzelaktien-Satellit"
    last_point = result.value_history[-1]
    assert last_point.total_value > 0
    total_weight = sum(last_point.ticker_weights.values())
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")
    assert all(units >= 0 for units in result.holdings.values())
    for ticker in EINZELAKTIEN_SATELLIT_TICKER:
        assert ticker in result.holdings
