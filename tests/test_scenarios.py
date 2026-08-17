"""Tests für zeitabhängige Auswertungs-Szenarien (``strategies.gewichte_fn``).

Erster Block: eine handgerechnete Zwei-Töpfe-Strategie mit einer einfachen
monatsabhängigen Regel, um die generische Engine-Mechanik (``weights_at``/
``gewichte_fn``) exakt zu verifizieren - analog zu ``test_engine.py``s
SIMPLE_STRATEGY, aber mit dynamischen statt konstanten Ziel-Gewichten.

Zweiter Block: Smoke-Tests für die konkreten Szenarien aus ``scenarios.py``
(Sell in May, Buy & Hold, SMA-Crossover) gegen die reale Barbell-Instrumente-
Struktur.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.history_store import PriceRow
from boersenspiel.scenarios import (
    BUY_AND_HOLD,
    BUY_THE_DIP,
    CHART_SMA_CROSSOVER,
    COST_AVERAGE_ENTRY,
    CUT_LOSSES,
    MOMENTUM_ROTATION,
    SANTA_CLAUS_RALLY,
    SELL_IN_MAY,
    VOLATILITY_TARGET,
    buy_the_dip_gewichte,
    chart_sma_crossover_gewichte,
    cost_average_gewichte,
    cut_losses_gewichte,
    momentum_rotation_gewichte,
    santa_claus_rally_gewichte,
    sell_in_may_gewichte,
    volatility_target_gewichte,
)
from boersenspiel.strategies import BARBELL_20_80, Strategy, Topf


def _monats_regel(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    """Volles Gewicht auf T1 im Mai, sonst 50/50 - fuer handgerechnete Werte."""
    if rows[i].date.month == 5:
        return {"T1": Decimal("1"), "T2": Decimal("0")}
    return {"T1": Decimal("0.5"), "T2": Decimal("0.5")}


DYNAMIC_STRATEGY = Strategy(
    name="Test-Dynamisch",
    startkapital=Decimal("1002"),
    toepfe=[
        Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T1": Decimal("1")}),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T2": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=_monats_regel,
)


def test_dynamic_gewichte_fn_drives_initial_buy_and_rebalance():
    rows = [
        # Initialkauf im April -> Regel liefert 50/50, wie bei einer konstanten
        # Strategie: 1002 - 2*1 Gebuehr = 1000 investierbar, je 500 -> 5 Einheiten
        # zu Kurs 100.
        PriceRow(date(2024, 4, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        # Mai: Regel schaltet auf 100% T1 / 0% T2 um, Kurse unveraendert (100/100)
        # -> Ist-Gewicht TopfX ist 50%, Ziel jetzt 100% -> Abweichung 50pp > 5pp
        # -> volles Rebalancing in T1.
        PriceRow(date(2024, 5, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
    ]
    result = simulate(rows, DYNAMIC_STRATEGY)

    assert result.holdings["T2"] == Decimal("0")
    # Rebalancing-Diffs werden gegen den Depotwert VOR dem Trade berechnet (500/500):
    # T2 komplett verkauft (Ziel 0%, -500), T1 um +500 auf Zielwert 1000 aufgestockt
    # -> 5 (Bestand) + 500/100 (Zukauf) = 10 Einheiten T1.
    assert result.holdings["T1"] == Decimal("10")
    assert result.last_rebalance_date == date(2024, 5, 1)


def test_dynamic_gewichte_fn_no_rebalance_when_regime_unchanged():
    rows = [
        PriceRow(date(2024, 4, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        # Juni: Regel liefert wieder 50/50 (wie beim Initialkauf) und Kurse sind
        # unveraendert -> keine Abweichung -> kein Rebalancing.
        PriceRow(date(2024, 6, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
    ]
    result = simulate(rows, DYNAMIC_STRATEGY)
    assert result.last_rebalance_date is None
    assert result.holdings["T1"] == Decimal("5")
    assert result.holdings["T2"] == Decimal("5")


def test_static_strategy_unaffected_by_gewichte_fn_default():
    """Ohne gewichte_fn (None) ist das Verhalten identisch zur bisherigen
    ausschließlich konstanten Gewichten - Regressionsschutz für BARBELL_20_80."""
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
    ]
    result = simulate(rows, BARBELL_20_80)
    assert result.strategy_name == "Barbell 20/80"
    assert all(units > 0 for units in result.holdings.values())


def _sample_rows() -> list[PriceRow]:
    prices_by_ticker = {
        "EUNL": Decimal("80"),
        "EUNA": Decimal("5"),
        "4GLD": Decimal("60"),
        "LYMS": Decimal("20"),
        "SEMI": Decimal("45"),
        "EIMI": Decimal("30"),
        "BTC-EUR": Decimal("40000"),
    }
    rows: list[PriceRow] = []
    d = date(2024, 1, 1)
    for week in range(60):
        prices = {t: p + Decimal(week) for t, p in prices_by_ticker.items()}
        rows.append(PriceRow(date.fromordinal(d.toordinal() + week * 7), prices))
    return rows


def test_sell_in_may_shifts_fully_defensive_in_summer_months():
    rows = _sample_rows()
    mai_row = next(r for r in rows if r.date.month == 5)
    i = rows.index(mai_row)
    gewichte = sell_in_may_gewichte(rows, i)
    assert gewichte["LYMS"] == Decimal("0")
    assert gewichte["SEMI"] == Decimal("0")
    assert gewichte["EIMI"] == Decimal("0")
    assert gewichte["BTC-EUR"] == Decimal("0")
    assert gewichte["EUNL"] == Decimal("0.50")

    dezember_row = next(r for r in rows if r.date.month == 12)
    j = rows.index(dezember_row)
    normal = sell_in_may_gewichte(rows, j)
    assert normal["LYMS"] == Decimal("0.80") * Decimal("0.40")


def test_sell_in_may_scenario_runs_end_to_end():
    result = simulate(_sample_rows(), SELL_IN_MAY)
    assert result.strategy_name == "Börsenweisheit: Sell in May"
    last_point = result.value_history[-1]
    assert last_point.total_value > 0
    total_weight = sum(last_point.ticker_weights.values())
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")
    # Im Sommer sollten Trades stattgefunden haben (Umschichtung in den Sicherheits-Topf).
    assert len(result.trades) > len(BARBELL_20_80.alle_ticker_gewichte())


def test_buy_and_hold_never_rebalances():
    result = simulate(_sample_rows(), BUY_AND_HOLD)
    assert result.last_rebalance_date is None
    reasons = {t.reason for t in result.trades}
    assert reasons <= {"initial_buy", "december_harvest", "december_harvest_rebuy"}


def test_chart_sma_crossover_defaults_to_normal_weights_without_enough_history():
    rows = _sample_rows()[:5]
    gewichte = chart_sma_crossover_gewichte(rows, 4)
    # Ticker-Gewicht am Gesamtdepot = Topf-A-Gewicht (0.20) * Sub-Gewicht (0.50).
    assert gewichte["EUNL"] == Decimal("0.10")


def test_chart_sma_crossover_scenario_runs_end_to_end():
    result = simulate(_sample_rows(), CHART_SMA_CROSSOVER)
    assert result.strategy_name == "Charttechnik: SMA-Crossover (10/40 Wochen)"
    last_point = result.value_history[-1]
    total_weight = sum(last_point.ticker_weights.values())
    assert abs(total_weight - Decimal("1")) < Decimal("0.0001")


# --- Jahresendrallye (Santa-Claus-Rally) -------------------------------------------


def test_santa_claus_rally_switches_aggressive_in_december_and_january():
    rows = _sample_rows()
    dezember_row = next(r for r in rows if r.date.month == 12)
    i = rows.index(dezember_row)
    gewichte = santa_claus_rally_gewichte(rows, i)
    # 95% Wachstum, gleichmaessig auf die Sub-Gewichte des Wachstums-Topfs verteilt.
    assert gewichte["LYMS"] == Decimal("0.95") * Decimal("0.40")
    assert gewichte["EUNL"] == Decimal("0.05") * Decimal("0.50")

    juni_row = next(r for r in rows if r.date.month == 6)
    j = rows.index(juni_row)
    normal = santa_claus_rally_gewichte(rows, j)
    assert normal["LYMS"] == Decimal("0.80") * Decimal("0.40")


def test_santa_claus_rally_scenario_runs_end_to_end():
    result = simulate(_sample_rows(), SANTA_CLAUS_RALLY)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")


# --- Antizyklisch kaufen (Buy the Dip) ----------------------------------------------


def _rows_with_dip() -> list[PriceRow]:
    """EUNL faellt in Woche 20 um mehr als 10% unter sein 20-Wochen-Hoch."""
    rows = []
    d = date(2024, 1, 1)
    for week in range(25):
        eunl = Decimal("100") if week < 20 else Decimal("85")
        rows.append(
            PriceRow(
                date.fromordinal(d.toordinal() + week * 7),
                {
                    "EUNL": eunl,
                    "EUNA": Decimal("5"),
                    "4GLD": Decimal("60"),
                    "LYMS": Decimal("20"),
                    "SEMI": Decimal("45"),
                    "EIMI": Decimal("30"),
                    "BTC-EUR": Decimal("40000"),
                },
            )
        )
    return rows


def test_buy_the_dip_switches_aggressive_after_drawdown():
    rows = _rows_with_dip()
    gewichte_vor_dip = buy_the_dip_gewichte(rows, 19)
    assert gewichte_vor_dip["LYMS"] == Decimal("0.80") * Decimal("0.40")

    gewichte_nach_dip = buy_the_dip_gewichte(rows, 20)
    assert gewichte_nach_dip["LYMS"] == Decimal("0.95") * Decimal("0.40")


def test_buy_the_dip_scenario_runs_end_to_end():
    result = simulate(_rows_with_dip(), BUY_THE_DIP)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")


# --- Verluste begrenzen (Trailing-Stop je Wachstums-Instrument) ---------------------


def _rows_with_single_loser() -> list[PriceRow]:
    """LYMS faellt um mehr als 15% unter sein Hoch, SEMI/EIMI/BTC-EUR bleiben flach."""
    rows = []
    d = date(2024, 1, 1)
    for week in range(25):
        lyms = Decimal("20") if week < 20 else Decimal("16")
        rows.append(
            PriceRow(
                date.fromordinal(d.toordinal() + week * 7),
                {
                    "EUNL": Decimal("80"),
                    "EUNA": Decimal("5"),
                    "4GLD": Decimal("60"),
                    "LYMS": lyms,
                    "SEMI": Decimal("45"),
                    "EIMI": Decimal("30"),
                    "BTC-EUR": Decimal("40000"),
                },
            )
        )
    return rows


def test_cut_losses_zeroes_only_the_losing_instrument():
    rows = _rows_with_single_loser()
    gewichte = cut_losses_gewichte(rows, 20)
    assert gewichte["LYMS"] == Decimal("0")
    # SEMI (anderes Wachstums-Instrument) bleibt unveraendert bei seinem normalen Gewicht.
    assert gewichte["SEMI"] == Decimal("0.80") * Decimal("0.30")
    # Das freigewordene LYMS-Gewicht (0.80*0.40=0.32) fliesst anteilig in den
    # Sicherheits-Topf, EUNL bekommt davon 50%.
    erwartetes_eunl_gewicht = Decimal("0.20") * Decimal("0.50") + Decimal("0.32") * Decimal("0.50")
    assert gewichte["EUNL"] == erwartetes_eunl_gewicht


def test_cut_losses_scenario_runs_end_to_end():
    result = simulate(_rows_with_single_loser(), CUT_LOSSES)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")


# --- Momentum-/Relative-Stärke-Rotation ----------------------------------------------


def _rows_with_momentum_spread() -> list[PriceRow]:
    """LYMS und BTC-EUR steigen ueber 12 Wochen deutlich, SEMI/EIMI bleiben flach."""
    rows = []
    d = date(2024, 1, 1)
    for week in range(15):
        rows.append(
            PriceRow(
                date.fromordinal(d.toordinal() + week * 7),
                {
                    "EUNL": Decimal("80"),
                    "EUNA": Decimal("5"),
                    "4GLD": Decimal("60"),
                    "LYMS": Decimal("20") + Decimal(week) * Decimal("2"),
                    "SEMI": Decimal("45"),
                    "EIMI": Decimal("30"),
                    "BTC-EUR": Decimal("40000") + Decimal(week) * Decimal("2000"),
                },
            )
        )
    return rows


def test_momentum_rotation_overweights_top_performers():
    rows = _rows_with_momentum_spread()
    gewichte = momentum_rotation_gewichte(rows, 12)
    assert gewichte["LYMS"] == Decimal("0.80") / 2
    assert gewichte["BTC-EUR"] == Decimal("0.80") / 2
    assert gewichte["SEMI"] == Decimal("0")
    assert gewichte["EIMI"] == Decimal("0")
    # Sicherheits-Topf bleibt bei den normalen Barbell-Gewichten.
    assert gewichte["EUNL"] == Decimal("0.20") * Decimal("0.50")


def test_momentum_rotation_falls_back_to_normal_without_enough_history():
    rows = _rows_with_momentum_spread()[:5]
    gewichte = momentum_rotation_gewichte(rows, 4)
    assert gewichte["LYMS"] == Decimal("0.80") * Decimal("0.40")


def test_momentum_rotation_scenario_runs_end_to_end():
    result = simulate(_rows_with_momentum_spread(), MOMENTUM_ROTATION)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")


# --- Volatilitätsbasierte Aktienquote --------------------------------------------------


def _rows_with_high_volatility() -> list[PriceRow]:
    """EUNL schwankt stark zwischen 70 und 100 -> hohe woechentliche Volatilitaet."""
    rows = []
    d = date(2024, 1, 1)
    for week in range(15):
        eunl = Decimal("100") if week % 2 == 0 else Decimal("70")
        rows.append(
            PriceRow(
                date.fromordinal(d.toordinal() + week * 7),
                {
                    "EUNL": eunl,
                    "EUNA": Decimal("5"),
                    "4GLD": Decimal("60"),
                    "LYMS": Decimal("20"),
                    "SEMI": Decimal("45"),
                    "EIMI": Decimal("30"),
                    "BTC-EUR": Decimal("40000"),
                },
            )
        )
    return rows


def test_volatility_target_reduces_growth_quote_when_volatile():
    rows = _rows_with_high_volatility()
    gewichte = volatility_target_gewichte(rows, 14)
    wachstumsquote = gewichte["LYMS"] / Decimal("0.40")
    assert wachstumsquote < Decimal("0.80")
    assert wachstumsquote == Decimal("0.50")  # bei dieser Schwankungsbreite am unteren Anschlag


def test_volatility_target_falls_back_to_normal_without_enough_history():
    rows = _rows_with_high_volatility()[:5]
    gewichte = volatility_target_gewichte(rows, 4)
    assert gewichte["LYMS"] == Decimal("0.80") * Decimal("0.40")


def test_volatility_target_scenario_runs_end_to_end():
    result = simulate(_rows_with_high_volatility(), VOLATILITY_TARGET)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")


# --- Cost-Average-Einstieg ------------------------------------------------------------


def test_cost_average_gewichte_ramps_linearly_then_stays_normal():
    rows = _sample_rows()
    gewichte_start = cost_average_gewichte(rows, 0)
    assert gewichte_start["LYMS"] == Decimal("0")  # Woche 0 -> 0% Wachstum, komplett defensiv
    assert gewichte_start["EUNL"] == Decimal("0.50")

    gewichte_mitte = cost_average_gewichte(rows, 5)
    # Nach halber Rampe (5 von 10 Wochen) liegt die Wachstumsquote bei 40% (halb 80%).
    assert gewichte_mitte["LYMS"] == Decimal("0.40") * Decimal("0.40")

    gewichte_ende = cost_average_gewichte(rows, 10)
    assert gewichte_ende["LYMS"] == Decimal("0.80") * Decimal("0.40")


def test_cost_average_entry_scenario_runs_end_to_end():
    result = simulate(_sample_rows(), COST_AVERAGE_ENTRY)
    last_point = result.value_history[-1]
    assert abs(sum(last_point.ticker_weights.values()) - Decimal("1")) < Decimal("0.0001")
