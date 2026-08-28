"""Tests für die Strategie "Dividende & Value" und ihre zwei neuen Instrumente (#99).

Dividenden- und Value-Investing gehören zu den bekanntesten Börsenstrategien;
im bisherigen Instrumentenset gab es dafür kein einziges gezielt
ausgerichtetes Instrument. ``ISPA`` (Dividende) und ``IS3S`` (Value-Faktor)
sind deshalb neu - und mit ihnen die Aufteilung des Wochenabrufs auf zwei Tage,
weil das Alpha-Vantage-Tagesbudget vorher exakt ausgeschöpft war (die
Batch-Tests dazu stehen in ``tests/test_backfill_history.py``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.instruments import INSTRUMENTS
from boersenspiel.sources.alphavantage import ALPHAVANTAGE_SYMBOLS, USD_TICKERS
from boersenspiel.strategies import (
    DIVIDENDE_UND_VALUE,
    RUBRIK_FAKTOR,
    STRATEGIES,
)


def test_strategie_ist_registriert():
    assert DIVIDENDE_UND_VALUE in STRATEGIES
    assert DIVIDENDE_UND_VALUE.rubrik == RUBRIK_FAKTOR


def test_ticker_gewichte_summieren_zu_eins():
    assert sum(DIVIDENDE_UND_VALUE.alle_ticker_gewichte().values()) == Decimal("1")


def test_zwei_gleich_grosse_toepfe_je_ein_instrument():
    assert DIVIDENDE_UND_VALUE.alle_ticker_gewichte() == {
        "ISPA": Decimal("0.50"),
        "IS3S": Decimal("0.50"),
    }
    for topf in DIVIDENDE_UND_VALUE.toepfe:
        assert topf.gewicht_gesamt == Decimal("0.50")
        assert sum(topf.sub_gewichte.values()) == Decimal("1")


def test_nutzt_die_5_25_regel_wie_die_uebrigen_neueren_strategien():
    assert DIVIDENDE_UND_VALUE.rebalancing_schwelle_pp == Decimal("5")
    assert DIVIDENDE_UND_VALUE.rebalancing_schwelle_relativ == Decimal("0.25")


def test_neue_instrumente_haben_ein_symbol_mapping():
    """Ohne Eintrag in ALPHAVANTAGE_SYMBOLS liefert der Abruf stillschweigend
    "missing" - der Ticker stünde dann dauerhaft leer in der Historie."""
    for ticker in DIVIDENDE_UND_VALUE.alle_ticker_gewichte():
        assert ticker in ALPHAVANTAGE_SYMBOLS


def test_neue_instrumente_sind_in_euro_notiert():
    """Beide sind bewusst XETRA-Symbole (Konvention seit #64). Landete eines in
    USD_TICKERS, bräuchte es die Umrechnung - und hätte damit vor November 2014
    gar keinen Kurs, weil FX_WEEKLY erst dann beginnt (#62)."""
    for ticker in DIVIDENDE_UND_VALUE.alle_ticker_gewichte():
        assert ticker not in USD_TICKERS
        assert ALPHAVANTAGE_SYMBOLS[ticker].endswith(".DEX")


def test_steuerattribute_der_neuen_instrumente():
    """Gegen die Fondsanbieter-Angaben belegt (siehe Kommentar in
    instruments.py): ISPA ist die ausschüttende Dividenden-Anteilsklasse mit
    einer deutlich über dem Markt liegenden Ausschüttung, IS3S die
    thesaurierende Acc-Anteilsklasse. Beide sind Aktienfonds und bekommen
    damit die 30% Teilfreistellung."""
    ispa = INSTRUMENTS["ISPA"]
    assert ispa.ausschuettend is True
    assert ispa.thesaurierend is False
    assert ispa.dividendenrendite == Decimal("0.050")
    assert ispa.teilfreistellung == Decimal("0.30")
    assert ispa.ter > 0

    is3s = INSTRUMENTS["IS3S"]
    assert is3s.thesaurierend is True
    assert is3s.ausschuettend is False
    assert is3s.teilfreistellung == Decimal("0.30")
    assert is3s.ter > 0


def _rows() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"ISPA": Decimal("30"), "IS3S": Decimal("40")}),
        PriceRow(date(2024, 6, 1), {"ISPA": Decimal("33"), "IS3S": Decimal("44")}),
    ]


def test_runs_end_to_end():
    result = simulate(_rows(), DIVIDENDE_UND_VALUE)
    assert result.strategy_name == "Dividende & Value"
    assert result.value_history[-1].total_value > 0


def test_bestehende_strategien_halten_die_neuen_instrumente_nicht():
    """Regressionsschutz analog zu #64: die neuen Datenreihen dürfen die
    veröffentlichten Kennzahlen der bereits bestehenden Strategien nicht
    verschieben."""
    neu = {"ISPA", "IS3S"}
    for strategy in STRATEGIES:
        if strategy is DIVIDENDE_UND_VALUE:
            continue
        assert not (neu & set(strategy.alle_ticker_gewichte()))
