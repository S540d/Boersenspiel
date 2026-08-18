"""Tests für engine.simulate() mit von Hand vorgerechneten Erwartungswerten.

Die Haupt-Testfixture verwendet eine eigene, einfache Zwei-Instrumente-
Strategie (nicht Barbell) mit bewusst so gewählten Kursen, dass alle
Zwischenschritte (Initialkauf, Rebalancing, Dezember-Harvest,
Jahreswechsel-Reset) mit exakten Dezimalwerten von Hand nachvollzogen werden
können - siehe Kommentare für die Herleitung jedes Schritts.

Ein zweiter, kürzerer Test läuft zusätzlich gegen die echte Barbell-Strategie
aus strategies.py, um sicherzustellen, dass die Engine keine
Barbell-spezifischen Annahmen fest einprogrammiert hat.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.strategies import BARBELL_20_80, Strategy, Topf

SIMPLE_STRATEGY = Strategy(
    name="Test-Zwei-Toepfe",
    startkapital=Decimal("1002"),
    toepfe=[
        Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T1": Decimal("1")}),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T2": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("10"),
)


def _rows() -> list[PriceRow]:
    return [
        # Initialkauf: 1002 Startkapital - 2*1 Gebuehr = 1000 investierbar,
        # je 500 -> 5 Einheiten je Instrument zu Kurs 100.
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        # T1 verdoppelt sich (200), T2 faellt auf 50 -> TopfX-Gewicht springt
        # auf 1000/1250=80% -> Abweichung 30pp > 10pp -> Rebalancing.
        # Zugleich letzte Zeile des Jahres 2024 UND das Jahr 2024 ist in der
        # Historie abgeschlossen (2025 folgt noch) -> Dezember-Harvest greift
        # danach zusaetzlich: Freibetrag ist nach dem Rebalancing-Gewinn noch
        # nicht ausgeschoepft (Massnahme A) -> die verbliebene T1-Position
        # (einziger Gewinnwert, T2 liegt im Verlust) wird vollstaendig
        # verkauft und sofort zurueckgekauft, um den Freibetrag weiter zu
        # fuellen.
        PriceRow(date(2024, 1, 8), {"T1": Decimal("200"), "T2": Decimal("50")}),
        # Neues Jahr (2025) -> Freibetrag-Reset. T1 steigt weiter auf 500,
        # T2 bleibt bei 50 -> erneutes Rebalancing. Kein Dezember-Harvest hier:
        # 2025 ist das laufende (nicht abgeschlossene) Jahr und die Zeile liegt
        # nicht im Dezember, auch wenn es die einzige Zeile in 2025 ist.
        PriceRow(date(2025, 1, 6), {"T1": Decimal("500"), "T2": Decimal("50")}),
    ]


def test_simple_strategy_end_to_end_exact_values():
    result = simulate(_rows(), SIMPLE_STRATEGY)

    # Von Hand hergeleitete Endwerte (siehe Modul-Docstring-Herleitung im PR).
    # holdings sind vom Zeitpunkt des Harvests unabhaengig - der Harvest kauft
    # zum selben Kurs sofort zurueck.
    assert result.holdings["T1"] == Decimal("2.1875")
    assert result.holdings["T2"] == Decimal("21.875")

    # Steuerherleitung:
    # 2024-01-08 Rebalance-Verkauf T1 (1,875 Stueck zu 200, Kostenbasis 100/Stk):
    #   Gewinn 375-187,50-1=186,50 -> voll gegen den frischen Freibetrag
    #   verrechnet (freibetrag_verbleibend 1000 -> 813,50).
    # 2024-01-08 Harvest (Jahr 2024 abgeschlossen, da 2025 folgt): Freibetrag
    #   ist noch nicht ausgeschoepft (813,50 > 0) -> Massnahme A
    #   (Gewinnmitnahme). Einziger Gewinnwert ist die verbliebene T1-Position
    #   (3,125 Stueck zu 200, Kostenbasis 100/Stk, unrealisiert +312,50); die
    #   Zielgroesse (813,50 + 1 Gebuehr)/100=8,135 Stueck uebersteigt den
    #   Bestand -> komplette Position verkauft: Gewinn 625-312,50-1=311,50 ->
    #   voll gegen den Freibetrag verrechnet -> freibetrag_verbleibend 502.
    #   T2 liegt im Verlust und wird bei Massnahme A nicht angefasst.
    # 2025-01-06 Freibetrag-Reset auf 1000 (neues Jahr). Rebalance-Verkauf T1
    #   (0,9375 Stueck zu 500, Kostenbasis 200,32/Stk): Gewinn
    #   468,75-187,80-1=279,95 -> voll gegen den (neuen) Freibetrag verrechnet
    #   -> freibetrag_verbleibend 720,05. Kein Harvest 2025 (laufendes,
    #   unvollstaendiges Jahr).
    assert result.tax_status.year == 2025
    assert result.tax_status.freibetrag_verbleibend == Decimal("720.05")
    assert result.tax_status.freibetrag_verbraucht == Decimal("279.95")
    assert result.tax_status.verlustvortrag == Decimal("0")
    assert result.tax_status.kumulierte_steuer == Decimal("0")

    assert result.last_rebalance_date == date(2025, 1, 6)
    assert result.last_harvest_date == date(2024, 1, 8)

    # 2 Initialkauf-Trades + 2024-01-08 (2 Rebalance + 2 Harvest) + 2025-01-06
    # (2 Rebalance, kein Harvest im laufenden Jahr)
    assert len(result.trades) == 8


def test_initial_buy_deducts_fees_before_allocation():
    result = simulate(_rows()[:1], SIMPLE_STRATEGY)
    assert result.holdings["T1"] == Decimal("5")
    assert result.holdings["T2"] == Decimal("5")
    assert len(result.trades) == 2
    assert all(t.reason == "initial_buy" for t in result.trades)


def test_no_rebalance_when_weights_unchanged():
    rows = _rows()[:1] + [PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("100")})]
    result = simulate(rows, SIMPLE_STRATEGY)
    # Unveraenderte Kurse -> kein Rebalancing, keine Verluste -> kein Harvest
    assert len(result.trades) == 2
    assert result.last_rebalance_date is None
    assert result.last_harvest_date is None


def test_determinism_repeated_runs_produce_identical_results():
    rows = _rows()
    result_a = simulate(rows, SIMPLE_STRATEGY)
    result_b = simulate(rows, SIMPLE_STRATEGY)

    assert result_a.holdings == result_b.holdings
    assert result_a.tax_status == result_b.tax_status
    assert len(result_a.trades) == len(result_b.trades)
    assert [vp.total_value for vp in result_a.value_history] == [
        vp.total_value for vp in result_b.value_history
    ]


def test_barbell_strategy_runs_and_keeps_weights_consistent():
    """Smoke-Test mit der echten Pflichtenheft-Strategie (keine hartkodierte
    Barbell-Annahme in der Engine - dieselbe simulate()-Funktion wie oben)."""
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
            },
        ),
    ]
    result = simulate(rows, BARBELL_20_80)

    assert result.strategy_name == "Barbell 20/80"
    last_point = result.value_history[-1]
    assert last_point.total_value > 0
    total_weight = sum(last_point.ticker_weights.values())
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")
    assert all(units >= 0 for units in result.holdings.values())


# Regressionstests fuer Issue #10: Instrumente ohne Kurs verlieren nicht mehr
# stillschweigend ihren Kapitalanteil.

MISSING_PRICE_STRATEGY = Strategy(
    name="Test-Zwei-Toepfe-Missing",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T1": Decimal("1")}),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T2": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("100"),  # kein Rebalancing in diesen Tests
)


def test_missing_price_in_first_row_parks_capital_as_cash_and_invests_later():
    # T2 hat in der ersten Zeile noch keinen Kurs (z. B. vor seinem IPO). Der
    # dafuer vorgesehene Kapitalanteil bleibt als Cash geparkt statt zu
    # verfallen und wird investiert, sobald T2 erstmals einen Kurs hat.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("50")}),
    ]
    result = simulate(rows, MISSING_PRICE_STRATEGY)

    # Startkapital 1000 - 2*1 Gebuehr = 998 investierbar, je 499 -> T1 sofort,
    # T2 verzoegert zum Kurs 50 der zweiten Zeile.
    assert result.holdings["T1"] == Decimal("4.99")
    assert result.holdings["T2"] == Decimal("9.98")
    assert result.value_history[0].total_value == Decimal("998")
    assert result.value_history[1].total_value == Decimal("998")
    delayed_buys = [t for t in result.trades if t.reason == "delayed_initial_buy"]
    assert len(delayed_buys) == 1
    assert delayed_buys[0].ticker == "T2"


def test_missing_price_in_later_row_values_with_last_known_price():
    # T2 fehlt in der mittleren Zeile - die Position darf nicht aus der Summe
    # herausfallen, sondern wird mit dem letzten bekannten Kurs bewertet.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 15), {"T1": Decimal("100"), "T2": Decimal("100")}),
    ]
    result = simulate(rows, MISSING_PRICE_STRATEGY)

    values = [vp.total_value for vp in result.value_history]
    assert values == [Decimal("998"), Decimal("998"), Decimal("998")]
