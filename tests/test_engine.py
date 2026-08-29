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

from datetime import date, timedelta
from decimal import Decimal

from boersenspiel.engine import simulate
from boersenspiel.instruments import INSTRUMENTS, Instrument
from boersenspiel.history_store import PriceRow
from boersenspiel.strategies import BARBELL_20_80, Optimierungen, Strategy, Topf

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


# --- Optimierungs-Schalter (Optimierungen, #17) -----------------------------------------


def test_optimierungen_defaults_reproduce_baseline_result():
    # Ein explizit übergebenes Optimierungen() mit Default-Werten muss exakt dasselbe
    # Ergebnis liefern wie gar keine Übergabe (strategy.optimierungen greift dann).
    ohne_override = simulate(_rows(), SIMPLE_STRATEGY)
    mit_default = simulate(_rows(), SIMPLE_STRATEGY, Optimierungen())
    assert ohne_override.value_history[-1].total_value == mit_default.value_history[-1].total_value
    assert ohne_override.tax_status == mit_default.tax_status


def test_ordergebuehren_false_entfernt_alle_gebuehren():
    result = simulate(_rows(), SIMPLE_STRATEGY, Optimierungen(ordergebuehren=False))
    assert all(t.fee == Decimal(0) for t in result.trades)
    # Ohne Gebuehren wird das volle Startkapital investiert statt 1002 - 2*1 = 1000.
    assert result.value_history[0].total_value == Decimal("1002")


def test_rebalancing_false_unterlaesst_periodisches_rebalancing():
    result = simulate(_rows(), SIMPLE_STRATEGY, Optimierungen(rebalancing=False))
    assert all(t.reason != "rebalance" for t in result.trades)


def test_steueroptimierung_false_unterlaesst_dezember_harvest():
    result = simulate(_rows(), SIMPLE_STRATEGY, Optimierungen(steueroptimierung=False))
    harvest_reasons = {
        "freibetrag_gewinnmitnahme",
        "freibetrag_gewinnmitnahme_rebuy",
        "tax_loss_harvest",
        "tax_loss_harvest_rebuy",
    }
    assert not any(t.reason in harvest_reasons for t in result.trades)
    assert result.last_harvest_date is None


def test_besteuerung_false_laesst_steuerstatus_unveraendert():
    result = simulate(_rows(), SIMPLE_STRATEGY, Optimierungen(besteuerung=False))
    assert result.tax_status.kumulierte_steuer == Decimal(0)
    assert result.tax_status.verlustvortrag == Decimal(0)
    assert result.tax_status.freibetrag_verbraucht == Decimal(0)


def test_strategy_eigene_optimierungen_werden_ohne_override_verwendet():
    strategy_ohne_rebalancing = Strategy(
        name="Test-ohne-Rebalancing",
        startkapital=Decimal("1002"),
        toepfe=SIMPLE_STRATEGY.toepfe,
        ziel_topf=SIMPLE_STRATEGY.ziel_topf,
        ziel_gewicht=SIMPLE_STRATEGY.ziel_gewicht,
        rebalancing_schwelle_pp=Decimal("10"),
        optimierungen=Optimierungen(rebalancing=False),
    )
    result = simulate(_rows(), strategy_ohne_rebalancing)
    assert all(t.reason != "rebalance" for t in result.trades)


def test_fehlender_kurs_in_erster_zeile_wird_auf_vorhandene_instrumente_umgelegt():
    # T2 hat in der ersten Zeile noch keinen Kurs (z. B. vor seinem IPO). Sein
    # Zielanteil wird auf die vorhandenen Instrumente umgelegt, damit das Depot
    # voll investiert bleibt statt einen Teil unverzinst zu parken; sobald T2
    # erstmals einen Kurs hat, wird auf die eigentliche Zielverteilung gebracht.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("50")}),
    ]
    result = simulate(rows, MISSING_PRICE_STRATEGY)

    # Startkapital 1000 - 2*1 Gebuehr = 998 investierbar. Zeile 1: alles in T1
    # (9.98 Stueck zu 100), nichts geparkt. Zeile 2: T2 existiert -> zurueck auf
    # 50/50, also je 499 -> T1 4.99 Stueck, T2 9.98 Stueck zu 50.
    assert result.value_history[0].total_value == Decimal("998")
    assert result.value_history[1].total_value == Decimal("998")
    assert result.holdings["T1"] == Decimal("4.99")
    assert result.holdings["T2"] == Decimal("9.98")

    # Der Erstkauf des neu verfuegbaren Instruments laeuft ueber "neues_instrument"
    # und ist damit im Trade-Log von normaler Drift-Korrektur unterscheidbar.
    neu = [t for t in result.trades if t.reason == "neues_instrument"]
    assert {t.ticker for t in neu} == {"T1", "T2"}
    assert next(t for t in neu if t.ticker == "T2").side == "buy"


def test_neues_instrument_wird_auch_ohne_rebalancing_gekauft():
    # MISSING_PRICE_STRATEGY hat eine unerreichbar hohe Rebalancing-Schwelle, und
    # hier ist Rebalancing zusaetzlich ganz abgeschaltet: ein neu verfuegbares
    # Instrument ist trotzdem ein Erstkauf, kein Korrigieren von Drift - sonst
    # wuerde eine Strategie ein Instrument, das es bei Simulationsbeginn noch
    # nicht gab, nie halten.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("50")}),
    ]
    result = simulate(rows, MISSING_PRICE_STRATEGY, Optimierungen(rebalancing=False))

    assert result.holdings["T2"] == Decimal("9.98")


def test_ohne_jeden_kurs_in_erster_zeile_bleibt_kapital_geparkt():
    # Grenzfall: hat KEIN Instrument einen Kurs, gibt es nichts, worauf umgelegt
    # werden koennte - dann greift weiterhin das Parken als pending_cash.
    rows = [
        PriceRow(date(2024, 1, 1), {}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("100"), "T2": Decimal("50")}),
    ]
    result = simulate(rows, MISSING_PRICE_STRATEGY)

    assert result.value_history[0].total_value == Decimal("998")
    assert result.holdings["T1"] == Decimal("4.99")
    assert result.holdings["T2"] == Decimal("9.98")


REBALANCING_MISSING_PRICE_STRATEGY = Strategy(
    name="Test-Rebalancing-Missing",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T1": Decimal("1")}),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"T2": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("1"),  # niedrig genug, damit rebalanciert wird
)


def test_rebalancing_erhaelt_depotwert_wenn_ein_instrument_noch_keinen_kurs_hat():
    # Regressionstest: T2 existiert noch nicht (kein Kurs - wie Bitcoin vor 2009
    # oder Rivian vor dem IPO 2021), waehrend T1 sich verdoppelt und damit ein
    # Rebalancing ausloest. Frueher wurde T2 beim Rebalancing stillschweigend
    # uebersprungen: der T1-Verkauf lief, der zugehoerige T2-Kauf entfiel, und der
    # Erloes verschwand ersatzlos aus dem Depot (ueber eine lange Kurshistorie
    # schrumpfte das Depot dadurch woechentlich gegen 0 EUR).
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("200")}),
    ]
    result = simulate(rows, REBALANCING_MISSING_PRICE_STRATEGY)

    # 1000 - 2*1 Gebuehr = 998 investierbar. T2 hat keinen Kurs, sein Zielanteil
    # wird auf T1 umgelegt: 998 / 100 = 9.98 Stueck. Zeile 2 verdoppelt T1 auf 200
    # -> 9.98 * 200 = 1996. Frueher wurde hier rebalanciert, der T2-Kauf entfiel,
    # und der Verkaufserloes verschwand ersatzlos aus dem Depot.
    assert result.value_history[1].total_value == Decimal("1996")

    # T2 hat weiterhin keinen Kurs und darf deshalb auch nicht gehandelt worden sein.
    assert all(t.ticker != "T2" for t in result.trades)
    # Ist- und Zielverteilung stimmen ueberein (T1 haelt 100% der umgelegten
    # Zielgewichte), es gibt also nichts zu rebalancieren.
    assert all(t.reason != "rebalance" for t in result.trades)


def test_neu_verfuegbares_instrument_wird_auf_zielgewicht_gebracht():
    # Fortsetzung des Falls oben: sobald T2 erstmals einen Kurs hat, wird die
    # eigentliche 50/50-Zielverteilung hergestellt - der Depotwert bleibt dabei
    # unveraendert, es wird nur umgeschichtet.
    rows = [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("200")}),
        PriceRow(date(2024, 1, 15), {"T1": Decimal("200"), "T2": Decimal("50")}),
    ]
    result = simulate(rows, REBALANCING_MISSING_PRICE_STRATEGY)

    # T1 steht unveraendert bei 200, der Gesamtwert bleibt also 1996 - jetzt aber
    # je 998 auf T1 (4.99 Stueck) und T2 (998 / 50 = 19.96 Stueck) verteilt.
    assert result.value_history[2].total_value == Decimal("1996")
    assert result.holdings["T1"] == Decimal("4.99")
    assert result.holdings["T2"] == Decimal("19.96")


# --- Paket A: Steuerkorrektheit (#37/#38/#39, siehe #46) --------------------


def test_teilfreistellung_reduziert_steuerpflichtigen_gewinn_um_30_prozent():
    # EUNL (Aktienfonds-ETF, 30% Teilfreistellung) vs. 4GLD (kein Fonds, 0%
    # Teilfreistellung) - sonst exakt dieselben Kurse/Gewichte wie die
    # SIMPLE_STRATEGY-Fixture oben, damit sich der Effekt isoliert nachrechnen
    # lässt: derselbe Rebalance-Verkauf, der ohne Teilfreistellung einen
    # Gewinn von 186,50 ergäbe (siehe test_simple_strategy_...), wird jetzt
    # nur zu 70% (130,55) gegen den Freibetrag verrechnet.
    strategy = Strategy(
        name="Test-Teilfreistellung",
        startkapital=Decimal("1002"),
        toepfe=[
            Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"EUNL": Decimal("1")}),
            Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"4GLD": Decimal("1")}),
        ],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("0.5"),
        rebalancing_schwelle_pp=Decimal("10"),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"EUNL": Decimal("100"), "4GLD": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"EUNL": Decimal("200"), "4GLD": Decimal("50")}),
    ]
    result = simulate(rows, strategy)

    assert result.tax_status.freibetrag_verbleibend == Decimal("869.45")
    assert result.tax_status.kumulierte_steuer == Decimal("0")


def test_vorabpauschale_verbraucht_freibetrag_ohne_verkauf():
    # EUNL ist thesaurierend - am Jahresende (hier: letzte Zeile 2024, mit
    # Optimierungen(steueroptimierung=False), um den Effekt vom Dezember-
    # Harvest zu isolieren) greift die vereinfachte Vorabpauschale, obwohl
    # keine einzige Position verkauft wird.
    # Wert Jahresbeginn: 9,99 Einheiten * 100 = 999.
    # Wert an der Harvest-Zeile: 9,99 * 150 = 1498,50 -> Wertsteigerung 499,50.
    # Vorabpauschale = min(999 * 0,02 * 0,70, 499,50) = 13,986.
    # Nach 30% Teilfreistellung steuerpflichtig: 13,986 * 0,7 = 9,7902.
    strategy = Strategy(
        name="Test-Vorabpauschale",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"EUNL": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(steueroptimierung=False, fondskosten=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"EUNL": Decimal("100")}),
        PriceRow(date(2024, 12, 30), {"EUNL": Decimal("150")}),
    ]
    result = simulate(rows, strategy)

    assert result.holdings["EUNL"] == Decimal("9.99")  # keine Trades ausser Initialkauf
    assert result.tax_status.freibetrag_verbleibend == Decimal("990.2098")
    assert result.tax_status.kumulierte_steuer == Decimal("0")


def test_dividende_wird_als_cash_gutgeschrieben_und_versteuert():
    # KO (ausschuettend, keine Teilfreistellung) - am Jahresende (letzte Zeile
    # 2024, mit Optimierungen(steueroptimierung=False), um den Effekt vom
    # Dezember-Harvest zu isolieren) greift die pauschale Dividendenrendite
    # (2,5% p.a.), obwohl der Kurs unveraendert bleibt und keine Position
    # verkauft wird.
    # Wert Jahresbeginn: 9,99 Einheiten * 100 = 999.
    # Dividende = 999 * 0,030 = 29,97 (KO fuehrt seit #74 eine eigene
    # Ausschuettungsrendite von 3,0% statt des Pauschal-Platzhalters von 2,5%),
    # komplett steuerpflichtig (0% Teilfreistellung fuer Einzelaktien) und in
    # voller Hoehe gegen den Freibetrag verrechnet: 1000 - 29,97 = 970,03.
    # Die Dividende wird als pending_cash gutgeschrieben, aber (mangels
    # weiterer Kurszeile) nicht mehr reinvestiert - die Stueckzahl bleibt
    # unveraendert, der ausgewiesene Portfoliowert steigt trotzdem um die
    # Dividende (999 + 29,97 = 1028,97).
    strategy = Strategy(
        name="Test-Dividende",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"KO": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(steueroptimierung=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"KO": Decimal("100")}),
        PriceRow(date(2024, 12, 30), {"KO": Decimal("100")}),
    ]
    result = simulate(rows, strategy)

    assert result.holdings["KO"] == Decimal("9.99")  # keine Trades ausser Initialkauf
    assert result.tax_status.freibetrag_verbleibend == Decimal("970.03")
    assert result.tax_status.kumulierte_steuer == Decimal("0")
    assert result.value_history[-1].total_value == Decimal("1028.97")


def test_dividende_bleibt_aus_wenn_besteuerung_deaktiviert_ist():
    # opt.besteuerung=False laesst process_realized_gain() fruehzeitig
    # zurueckkehren (Freibetrag/Steuer bleiben unveraendert), der reale
    # Cash-Zufluss (und damit der Portfoliowert) bleibt aber bestehen - die
    # Dividende ist ein echter Kapitalertrag, kein reines Steuerkonstrukt wie
    # die Vorabpauschale.
    strategy = Strategy(
        name="Test-Dividende-ohne-Besteuerung",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"KO": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(steueroptimierung=False, besteuerung=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"KO": Decimal("100")}),
        PriceRow(date(2024, 12, 30), {"KO": Decimal("100")}),
    ]
    result = simulate(rows, strategy)

    assert result.tax_status.freibetrag_verbleibend == Decimal("1000")
    assert result.tax_status.kumulierte_steuer == Decimal("0")
    assert result.value_history[-1].total_value == Decimal("1028.97")


def test_btc_gewinn_innerhalb_spekulationsfrist_landet_in_eigener_freigrenze():
    # BTC-EUR (Spekulationsfrist 365 Tage) neben 4GLD (normale Abgeltungsteuer)
    # - derselbe Rebalance-Verkauf wie in test_simple_strategy_... (Gewinn
    # 186,50), aber nur 7 Tage Haltedauer: landet im getrennten
    # Freigrenzen-Topf statt im Sparerpauschbetrag - dieser bleibt
    # unangetastet bei 1000.
    strategy = Strategy(
        name="Test-BTC-innerhalb-Frist",
        startkapital=Decimal("1002"),
        toepfe=[
            Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"BTC-EUR": Decimal("1")}),
            Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"4GLD": Decimal("1")}),
        ],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("0.5"),
        rebalancing_schwelle_pp=Decimal("10"),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"BTC-EUR": Decimal("100"), "4GLD": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"BTC-EUR": Decimal("200"), "4GLD": Decimal("50")}),
    ]
    result = simulate(rows, strategy)

    # Der BTC-Gewinn (186,50, unter der 1000€-Freigrenze) bleibt steuerfrei,
    # verbraucht aber NICHT den Sparerpauschbetrag der Kapitalertraege.
    assert result.tax_status.freibetrag_verbleibend == Decimal("1000")
    assert result.tax_status.kumulierte_steuer == Decimal("0")


def test_btc_gewinn_nach_ablauf_der_spekulationsfrist_ist_steuerfrei():
    # Gleicher Rebalance-Mechanismus, aber die BTC-Position wird 367 Tage
    # gehalten (> 365 Tage Spekulationsfrist) und der Gewinn faellt deutlich
    # groesser aus (Kurssprung 100 -> 2000) - wuerde er (fehlerhaft) besteuert,
    # sowohl die Freigrenze als auch der Sparerpauschbetrag wuerden deutlich
    # sichtbare Steuer ausloesen. Stattdessen bleibt er komplett steuerfrei.
    strategy = Strategy(
        name="Test-BTC-ausserhalb-Frist",
        startkapital=Decimal("1002"),
        toepfe=[
            Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"BTC-EUR": Decimal("1")}),
            Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"4GLD": Decimal("1")}),
        ],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("0.5"),
        rebalancing_schwelle_pp=Decimal("10"),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"BTC-EUR": Decimal("100"), "4GLD": Decimal("100")}),
        PriceRow(date(2025, 1, 2), {"BTC-EUR": Decimal("2000"), "4GLD": Decimal("100")}),
    ]
    result = simulate(rows, strategy)

    assert result.tax_status.kumulierte_steuer == Decimal("0")
    assert result.tax_status.freibetrag_verbleibend == Decimal("1000")


# --- Sofortverkauf zum Stichtag (Liquidationswert nach Steuer) --------------


def test_liquidationswert_zieht_steuer_auf_unrealisierten_gewinn_ab():
    # Einzige Position 4GLD (0% Teilfreistellung), keine Rebalance-Verkaeufe,
    # keine Ordergebuehr (isoliert die Steuerrechnung) - der komplette Gewinn
    # bleibt bis zum Schluss unrealisiert und steckt nur in
    # liquidationswert_nach_steuer, nicht in tax_status.kumulierte_steuer.
    # Kauf: 100.000 / 100 = 1000 Einheiten, cost_total = 100.000.
    # Verkaufswert: 1000 * 300 = 300.000 -> Gewinn 200.000.
    # Nach Sparerpauschbetrag (1000): 199.000 * 26,375% = 52.486,25 Steuer.
    strategy = Strategy(
        name="Test-Liquidation-Kapitalertrag",
        startkapital=Decimal("100000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"4GLD": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(ordergebuehren=False, steueroptimierung=False, fondskosten=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"4GLD": Decimal("100")}),
        PriceRow(date(2024, 6, 1), {"4GLD": Decimal("300")}),
    ]
    result = simulate(rows, strategy)

    assert result.value_history[-1].total_value == Decimal("300000")
    assert result.tax_status.kumulierte_steuer == Decimal("0")  # nichts realisiert
    assert result.liquidationsgebuehren == Decimal("0")
    assert result.liquidationssteuer == Decimal("52486.25")
    assert result.liquidationswert_nach_steuer == Decimal("247513.75")


def test_liquidationswert_zieht_verkaufsgebuehr_je_gehaltenem_instrument_ab():
    # Zwei gehaltene Instrumente -> zwei Verkaufsgebuehren (ORDERGEBUEHR = 1
    # EUR je Trade). besteuerung=False isoliert den Gebuehren-Effekt: keine
    # Steuer, nur die Gebuehr mindert den Nettowert.
    strategy = Strategy(
        name="Test-Liquidation-Gebuehr",
        startkapital=Decimal("1000"),
        toepfe=[
            Topf(name="TopfX", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"4GLD": Decimal("1")}),
            Topf(name="TopfY", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"EUNA": Decimal("1")}),
        ],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("0.5"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(besteuerung=False, steueroptimierung=False, fondskosten=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"4GLD": Decimal("100"), "EUNA": Decimal("100")}),
        PriceRow(date(2024, 6, 1), {"4GLD": Decimal("150"), "EUNA": Decimal("120")}),
    ]
    result = simulate(rows, strategy)

    assert result.liquidationssteuer == Decimal("0")
    assert result.liquidationsgebuehren == Decimal("2")  # ORDERGEBUEHR * 2 Instrumente
    assert result.liquidationswert_nach_steuer == result.value_history[-1].total_value - Decimal("2")


def test_liquidationswert_beruecksichtigt_spekulationsfrist_von_btc():
    # BTC-EUR innerhalb der Spekulationsfrist (#37) mit einem Gewinn deutlich
    # ueber der Freigrenze (1000 EUR) - der GESAMTE Gewinn wird steuerpflichtig
    # (Kippgrenze, keine Teilfreistellung fuer BTC), landet aber im getrennten
    # Freigrenzen-Topf statt den Sparerpauschbetrag zu verbrauchen.
    # Kauf: 1000/100 = 10 Einheiten, cost_total = 1000. Verkauf: 10*300 = 3000
    # -> Gewinn 2000, ueber der 1000er-Freigrenze -> 2000 * 26,375% = 527,50.
    strategy = Strategy(
        name="Test-Liquidation-BTC-Frist",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"BTC-EUR": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(ordergebuehren=False, steueroptimierung=False, fondskosten=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"BTC-EUR": Decimal("100")}),
        PriceRow(date(2024, 6, 1), {"BTC-EUR": Decimal("300")}),
    ]
    result = simulate(rows, strategy)

    assert result.liquidationssteuer == Decimal("527.50")
    assert result.tax_status.freibetrag_verbleibend == Decimal("1000")  # unberuehrt
    assert result.liquidationswert_nach_steuer == Decimal("3000") - Decimal("527.50")


def test_liquidationswert_ohne_besteuerung_zieht_nur_gebuehren_ab():
    strategy = Strategy(
        name="Test-Liquidation-ohne-Besteuerung",
        startkapital=Decimal("100000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"4GLD": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(besteuerung=False, steueroptimierung=False, fondskosten=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"4GLD": Decimal("100")}),
        PriceRow(date(2024, 6, 1), {"4GLD": Decimal("300")}),
    ]
    result = simulate(rows, strategy)

    assert result.liquidationssteuer == Decimal("0")
    # ORDERGEBUEHR faellt unabhaengig von opt.besteuerung an (reine
    # Steuerabschaltung, keine Gebuehrenbefreiung).
    assert result.liquidationsgebuehren == Decimal("1")
    assert result.liquidationswert_nach_steuer == result.value_history[-1].total_value - Decimal("1")


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


# --- Erstkauf darf kein verdecktes Rebalancing sein (#62) ---------------------

# Drei Instrumente, T3 kommt erst spaeter an den Markt. Die Gewichte sind
# bewusst ungleich (40/40/20), damit sich ein Erstkauf "nur T3 dazukaufen" von
# einem Voll-Rebalancing "alle drei auf Zielgewicht" unterscheiden laesst.
DRITTES_INSTRUMENT_STRATEGY = Strategy(
    name="Test-Spaeterer-Boersengang",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(
            name="TopfX",
            gewicht_gesamt=Decimal("0.8"),
            sub_gewichte={"T1": Decimal("0.5"), "T2": Decimal("0.5")},
        ),
        Topf(name="TopfY", gewicht_gesamt=Decimal("0.2"), sub_gewichte={"T3": Decimal("1")}),
    ],
    ziel_topf="TopfX",
    ziel_gewicht=Decimal("0.8"),
    rebalancing_schwelle_pp=Decimal("10"),
)

# Ohne Gebuehren/Steuer, damit die Stueckzahlen von Hand exakt aufgehen.
_OHNE_REIBUNG = dict(ordergebuehren=False, besteuerung=False, steueroptimierung=False)


def _drittes_instrument_rows() -> list[PriceRow]:
    return [
        # T3 gibt es noch nicht -> sein 20%-Ziel wird auf T1/T2 umgelegt
        # (je 0.4/0.8 = 50%): 1000 EUR -> je 500 EUR -> je 5 Stueck zu 100.
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100"), "T2": Decimal("100")}),
        # T1 verdoppelt sich. Depot: T1 1000, T2 500, gesamt 1500.
        PriceRow(date(2024, 1, 8), {"T1": Decimal("200"), "T2": Decimal("100")}),
        # T3 ist erstmals handelbar (Boersengang).
        PriceRow(date(2024, 1, 15), {"T1": Decimal("200"), "T2": Decimal("100"), "T3": Decimal("50")}),
    ]


def test_erstkauf_ohne_rebalancing_laesst_die_verhaeltnisse_des_altbestands_unangetastet():
    """Bei ``rebalancing=False`` finanziert der Erstkauf NUR das neue
    Instrument - anteilig aus allen bestehenden Positionen, ohne deren
    Verhaeltnis zueinander zu veraendern.

    Vorher loeste jeder Boersengang ein komplettes Rebalancing aus, auch wenn
    Rebalancing fuer die Strategie ausgeschaltet war. Ueber die
    20-Jahres-Historie waren das 27 verdeckte Voll-Rebalancings, wodurch das
    Szenario "Time in the market beats timing the market" von der
    rebalancierenden Barbell-Strategie nicht mehr zu unterscheiden war.
    """
    result = simulate(
        _drittes_instrument_rows(),
        DRITTES_INSTRUMENT_STRATEGY,
        Optimierungen(rebalancing=False, **_OHNE_REIBUNG),
    )

    # T3 bekommt sein Zielgewicht: 20% von 1500 = 300 EUR / 50 = 6 Stueck.
    assert result.holdings["T3"] == Decimal("6")
    # Die restlichen 1200 EUR verteilen sich im BISHERIGEN Verhaeltnis 1000:500,
    # also 800 EUR T1 (= 4 Stueck zu 200) und 400 EUR T2 (= 4 Stueck zu 100).
    # (Die Zielanteile 0.8*1000/1500 bzw. 0.8*500/1500 sind periodisch, deshalb
    # auf Cent-Ebene statt exakt vergleichen.)
    assert abs(result.holdings["T1"] - Decimal("4")) < Decimal("0.0001")
    assert abs(result.holdings["T2"] - Decimal("4")) < Decimal("0.0001")
    # Gegenprobe auf die eigentliche Invariante: das Wertverhaeltnis T1:T2 ist
    # vor (1000:500) und nach dem Erstkauf (800:400) dasselbe.
    werte = result.value_history[-1].ticker_values
    assert abs(werte["T1"] / werte["T2"] - Decimal("2")) < Decimal("0.0001")
    assert abs(result.value_history[-1].total_value - Decimal("1500")) < Decimal("0.0001")


def test_erstkauf_mit_rebalancing_holt_weiterhin_alles_auf_zielgewicht():
    """Gegenprobe: fuer eine rebalancierende Strategie bleibt der Erstkauf
    ein vollwertiges Rebalancing auf die Zielgewichte."""
    result = simulate(
        _drittes_instrument_rows(),
        DRITTES_INSTRUMENT_STRATEGY,
        Optimierungen(rebalancing=True, **_OHNE_REIBUNG),
    )

    # Zielgewichte 40/40/20 auf 1500 EUR: 600 / 600 / 300.
    assert result.holdings["T1"] == Decimal("3")  # 600 / 200
    assert result.holdings["T2"] == Decimal("6")  # 600 / 100
    assert result.holdings["T3"] == Decimal("6")  # 300 / 50
    assert result.value_history[-1].total_value == Decimal("1500")


# --- Rebalancing-Trigger: "5/25-Regel" je Topf (#63, F5) ---------------------------
#
# Vorher pruefte der Trigger ausschliesslich ziel_topf (Topf A) mit einer rein
# absoluten Schwelle. Zwei Regressionstests dafuer, dass jetzt (a) JEDER Topf
# geprueft wird, nicht nur ziel_topf, und (b) eine relative Zusatzschwelle
# greift, die bei rebalancing_schwelle_relativ=1 (Default, unveraendertes
# Altverhalten) wirkungslos bleibt.

DREI_TOEPFE_STRATEGY = Strategy(
    name="Test-Drei-Toepfe",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(name="TopfA", gewicht_gesamt=Decimal("0.4"), sub_gewichte={"A": Decimal("1")}),
        Topf(name="TopfB", gewicht_gesamt=Decimal("0.4"), sub_gewichte={"B": Decimal("1")}),
        Topf(name="TopfC", gewicht_gesamt=Decimal("0.2"), sub_gewichte={"C": Decimal("1")}),
    ],
    ziel_topf="TopfA",
    ziel_gewicht=Decimal("0.4"),
    rebalancing_schwelle_pp=Decimal("10"),
    # rebalancing_schwelle_relativ bewusst NICHT gesetzt (Default 1) - dieser Test
    # isoliert die "jeder Topf statt nur ziel_topf"-Aenderung von der neuen
    # relativen Schwelle.
)


def test_5_25_regel_prueft_jeden_topf_nicht_nur_ziel_topf():
    rows = [
        # Initialkauf 1000 EUR (gebuehrenfrei) auf 40/40/20 -> A=400, B=400, C=200.
        PriceRow(date(2024, 1, 1), {"A": Decimal("100"), "B": Decimal("100"), "C": Decimal("100")}),
        # A unveraendert (400), B halbiert sich (200), C verdreifacht sich (600).
        # Gesamt 1200. Ist-Gewichte: A=400/1200=33.33% (Abweichung von 40% Ziel nur
        # 6.67pp, UNTER der 10pp-Schwelle - der alte, nur-ziel_topf-Trigger haette
        # HIER NICHT ausgeloest). B=200/1200=16.67% (Abweichung 23.33pp) und
        # C=600/1200=50% (Abweichung 30pp) liegen beide klar ueber 10pp.
        PriceRow(date(2024, 1, 8), {"A": Decimal("100"), "B": Decimal("50"), "C": Decimal("300")}),
    ]
    result = simulate(rows, DREI_TOEPFE_STRATEGY, Optimierungen(**_OHNE_REIBUNG))

    assert result.last_rebalance_date == date(2024, 1, 8)
    # Nach dem Rebalancing wieder exakt auf den Zielgewichten: 1200 * 40/40/20%.
    last = result.value_history[-1]
    assert last.ticker_values["A"] == Decimal("480")
    assert last.ticker_values["B"] == Decimal("480")
    assert last.ticker_values["C"] == Decimal("240")


KLEINER_TOPF_STRATEGY_BASIS = dict(
    name="Test-Kleiner-Topf",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(name="TopfA", gewicht_gesamt=Decimal("0.1"), sub_gewichte={"A": Decimal("1")}),
        Topf(name="TopfB", gewicht_gesamt=Decimal("0.9"), sub_gewichte={"B": Decimal("1")}),
    ],
    ziel_topf="TopfA",
    ziel_gewicht=Decimal("0.1"),
    rebalancing_schwelle_pp=Decimal("5"),
)


def _kleiner_topf_rows() -> list[PriceRow]:
    return [
        # Initialkauf 1000 EUR (gebuehrenfrei) auf 10/90 -> A=100 (1 Stk zu 100),
        # B=900 (10 Stk zu 90).
        PriceRow(date(2024, 1, 1), {"A": Decimal("100"), "B": Decimal("90")}),
        # A steigt auf 130 (Wert 130), B faellt auf 87 (Wert 870) -> Gesamt 1000,
        # ist_A=13%. Abweichung vom 10%-Ziel: 3pp - unter der absoluten 5pp-Schwelle,
        # aber 3pp / 10% Ziel = 30% relative Abweichung.
        PriceRow(date(2024, 1, 8), {"A": Decimal("130"), "B": Decimal("87")}),
    ]


def test_5_25_regel_relative_schwelle_loest_aus_wo_absolute_schwelle_nicht_reicht():
    # rebalancing_schwelle_relativ=0.25 (25%, Marktstandard-Anteil): fuer den
    # 10%-Topf ergibt das eine effektive Schwelle von min(5pp, 10%*25%=2.5pp) =
    # 2.5pp - die 3pp-Abweichung oben liegt darueber.
    strategy_mit_relativ = Strategy(
        **KLEINER_TOPF_STRATEGY_BASIS, rebalancing_schwelle_relativ=Decimal("0.25")
    )
    result = simulate(_kleiner_topf_rows(), strategy_mit_relativ, Optimierungen(**_OHNE_REIBUNG))
    assert result.last_rebalance_date == date(2024, 1, 8)


def test_5_25_regel_relativ_default_aendert_altverhalten_nicht():
    # Gegenprobe mit demselben Kursverlauf, aber OHNE gesetzte relative Schwelle
    # (Default 1 = 100%, faktisch wirkungslos): effektive Schwelle bleibt bei den
    # vollen 5pp absolut, die 3pp-Abweichung loest deshalb KEIN Rebalancing aus -
    # exakt das Verhalten vor #63.
    strategy_ohne_relativ = Strategy(**KLEINER_TOPF_STRATEGY_BASIS)
    result = simulate(_kleiner_topf_rows(), strategy_ohne_relativ, Optimierungen(**_OHNE_REIBUNG))
    assert result.last_rebalance_date is None


# --- Zielgewicht-Aenderungs-Trigger fuer gewichte_fn-Szenarien --------------
#
# Der Topf-Trigger oben prueft Ist- gegen Ziel-Gewicht je Topf. Eine
# gewichte_fn, die nur INNERHALB eines Topfs umschichtet (Topf-Summe bleibt
# gleich), loeste vor dieser Ergaenzung nie ein Rebalancing aus: Ist- und
# Ziel-Topfgewicht stimmten immer ueberein, weil "Ziel" pro Zeile direkt aus
# der (bereits verschobenen) current_weights abgeleitet wird. Betroffen waren
# u. a. das Momentum-Szenario, dessen Wertverlauf dadurch ueber weite Strecken
# bit-identisch mit dem zugrundeliegenden Barbell-Portfolio war.


def _rotations_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    if i == 0:
        return {"A": Decimal("0.5"), "X": Decimal("0.25"), "Y": Decimal("0.25")}
    # Ab Zeile 1 dreht sich die Zusammensetzung von TopfB komplett (X/Y
    # tauschen ihre Gewichte), TopfB bleibt aber unveraendert bei 50%.
    return {"A": Decimal("0.5"), "X": Decimal("0.4"), "Y": Decimal("0.1")}


ROTATIONS_STRATEGY = Strategy(
    name="Test-Rotation-Innerhalb-Topf",
    startkapital=Decimal("1000"),
    toepfe=[
        Topf(name="TopfA", gewicht_gesamt=Decimal("0.5"), sub_gewichte={"A": Decimal("1")}),
        Topf(
            name="TopfB",
            gewicht_gesamt=Decimal("0.5"),
            sub_gewichte={"X": Decimal("0.5"), "Y": Decimal("0.5")},
        ),
    ],
    ziel_topf="TopfA",
    ziel_gewicht=Decimal("0.5"),
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=_rotations_gewichte,
)


def test_zielgewicht_aenderung_innerhalb_eines_topfs_loest_rebalancing_aus():
    rows = [
        # Initialkauf 1000 EUR (gebuehrenfrei) auf 50/25/25 -> A=500, X=250, Y=250.
        PriceRow(date(2024, 1, 1), {"A": Decimal("100"), "X": Decimal("100"), "Y": Decimal("100")}),
        # Kurse UNVERAENDERT - TopfB-Ist bleibt exakt bei 50% (X=250+Y=250),
        # ein reiner Topf-Trigger saehe hier ueberhaupt keine Abweichung. Das
        # ZIEL innerhalb TopfB hat sich aber von 25/25 auf 40/10 verschoben
        # (15pp je Instrument, klar ueber der 5pp-Schwelle).
        PriceRow(date(2024, 1, 8), {"A": Decimal("100"), "X": Decimal("100"), "Y": Decimal("100")}),
    ]
    result = simulate(rows, ROTATIONS_STRATEGY, Optimierungen(**_OHNE_REIBUNG))

    assert result.last_rebalance_date == date(2024, 1, 8)
    last = result.value_history[-1]
    assert last.ticker_values["A"] == Decimal("500")
    assert last.ticker_values["X"] == Decimal("400")
    assert last.ticker_values["Y"] == Decimal("100")


# --- Ausschuettungsrendite je Instrument (#74) ------------------------------


def test_nicht_zahlende_einzelaktien_bekommen_keine_dividende():
    """Sieben der zehn Satelliten-Aktien zahlen real keine Dividende. Vor #74
    bekamen sie ueber ``ausschuettend=True`` trotzdem jaehrlich die
    Pauschalrendite gutgeschrieben - geschenktes Geld, das die Rendite des
    Satelliten-Topfs verzerrte."""
    for ticker in ("TSLA", "RIVN", "PLTR", "MSTR", "SEDG", "LITE", "S92"):
        assert INSTRUMENTS[ticker].ausschuettend is False, ticker

    strategy = Strategy(
        name="Test-Nichtzahler",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"TSLA": Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("100"),
        optimierungen=Optimierungen(steueroptimierung=False),
    )
    rows = [
        PriceRow(date(2024, 1, 1), {"TSLA": Decimal("100")}),
        PriceRow(date(2024, 12, 30), {"TSLA": Decimal("100")}),
    ]
    result = simulate(rows, strategy)

    # Kein Cash-Zufluss: der Wert bleibt exakt der Kaufwert (1000 - 1 Gebuehr).
    assert result.value_history[-1].total_value == Decimal("999")
    assert result.tax_status.freibetrag_verbleibend == Decimal("1000")


def test_ausschuettende_instrumente_nutzen_ihre_eigene_rendite():
    """Zwei ausschuettende Instrumente mit unterschiedlicher Rendite duerfen
    nicht denselben Betrag ausschuetten (vor #74 taten sie genau das)."""
    assert INSTRUMENTS["IQQ6"].dividendenrendite == Decimal("0.035")
    assert INSTRUMENTS["IUSA"].dividendenrendite == Decimal("0.013")

    def endwert(ticker: str) -> Decimal:
        strategy = Strategy(
            name=f"Test-{ticker}",
            startkapital=Decimal("1000"),
            toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={ticker: Decimal("1")})],
            ziel_topf="TopfX",
            ziel_gewicht=Decimal("1"),
            rebalancing_schwelle_pp=Decimal("100"),
            # fondskosten=False: dieser Test prueft ausschliesslich die
            # Ausschuettung, die TER der beiden ETFs wuerde den Endwert sonst
            # zusaetzlich mindern (siehe eigene TER-Tests unten).
            optimierungen=Optimierungen(steueroptimierung=False, fondskosten=False),
        )
        rows = [
            PriceRow(date(2024, 1, 1), {ticker: Decimal("100")}),
            PriceRow(date(2024, 12, 30), {ticker: Decimal("100")}),
        ]
        return simulate(rows, strategy).value_history[-1].total_value

    # 999 * 0,035 = 34,965 bzw. 999 * 0,013 = 12,987
    assert endwert("IQQ6") == Decimal("1033.965")
    assert endwert("IUSA") == Decimal("1011.987")


def test_ohne_hinterlegte_rendite_gilt_weiterhin_der_platzhalter():
    """Der Pauschal-Platzhalter bleibt der Rueckfallwert fuer ausschuettende
    Instrumente ohne eigenen Satz - #74 entfernt ihn nicht, es gilt nur nicht
    mehr derselbe Wert fuer alle."""
    inst = Instrument("XTEST", "Testinstrument", None, ausschuettend=True)
    assert inst.dividendenrendite is None
    INSTRUMENTS["XTEST"] = inst
    try:
        strategy = Strategy(
            name="Test-Platzhalter",
            startkapital=Decimal("1000"),
            toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={"XTEST": Decimal("1")})],
            ziel_topf="TopfX",
            ziel_gewicht=Decimal("1"),
            rebalancing_schwelle_pp=Decimal("100"),
            optimierungen=Optimierungen(steueroptimierung=False),
        )
        rows = [
            PriceRow(date(2024, 1, 1), {"XTEST": Decimal("100")}),
            PriceRow(date(2024, 12, 30), {"XTEST": Decimal("100")}),
        ]
        # 999 * DIVIDENDENRENDITE_PLATZHALTER (2,5%) = 24,975
        assert simulate(rows, strategy).value_history[-1].total_value == Decimal("1023.975")
    finally:
        del INSTRUMENTS["XTEST"]


# --- Laufende Fondskosten / TER (#76) ---------------------------------------


def _ter_strategie(ticker: str, fondskosten: bool) -> Strategy:
    return Strategy(
        name=f"Test-TER-{ticker}-{fondskosten}",
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="TopfX", gewicht_gesamt=Decimal("1"), sub_gewichte={ticker: Decimal("1")})],
        ziel_topf="TopfX",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
        optimierungen=Optimierungen(
            steueroptimierung=False, besteuerung=False, fondskosten=fondskosten
        ),
    )


def _ter_rows(ticker: str, wochen: int) -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1) + timedelta(weeks=i), {ticker: Decimal("100")})
        for i in range(wochen)
    ]


def test_ter_mindert_den_bestand_woechentlich_pro_rata():
    # EXXY: TER 0,46% p.a., thesaurierend (schuettet also nichts aus, was den
    # Endwert ueberlagern wuerde). Bei konstantem Kurs ueber 53 Zeilen
    # (= 52 Wochen Haltedauer, die erste Zeile ist der Kauftag) muss der
    # Bestand um (1 - 0,0046/52)^52 schrumpfen - von Hand nachgerechnet.
    rows = _ter_rows("EXXY", 53)
    result = simulate(rows, _ter_strategie("EXXY", fondskosten=True))

    einsatz = Decimal("999")  # 1000 minus 1 EUR Ordergebuehr
    erwartet = einsatz * (1 - Decimal("0.0046") / Decimal(52)) ** 52
    ist = result.value_history[-1].total_value
    assert abs(ist - erwartet) < Decimal("0.0001")
    # Groessenordnung: rund 0,46% weniger als ohne Kosten.
    assert Decimal("0.0044") < (einsatz - ist) / einsatz < Decimal("0.0047")


def test_ter_schalter_aus_reproduziert_das_verhalten_ohne_fondskosten():
    rows = _ter_rows("EXXY", 53)
    ohne = simulate(rows, _ter_strategie("EXXY", fondskosten=False))
    assert ohne.value_history[-1].total_value == Decimal("999")


def test_instrumente_ohne_ter_bleiben_unberuehrt():
    # Einzelaktien und physisches Gold tragen keine Fondsgebuehr - fuer sie
    # darf der Schalter nichts aendern.
    for ticker in ("TSLA", "4GLD", "BTC-EUR"):
        assert INSTRUMENTS[ticker].ter == Decimal("0"), ticker
        rows = _ter_rows(ticker, 53)
        mit = simulate(rows, _ter_strategie(ticker, fondskosten=True))
        ohne = simulate(rows, _ter_strategie(ticker, fondskosten=False))
        assert mit.value_history[-1].total_value == ohne.value_history[-1].total_value


def test_teure_und_guenstige_fonds_werden_unterschiedlich_belastet():
    # Der Kern des Befunds aus #76: die Saetze liegen um eine Groessenordnung
    # auseinander (IBCI 0,09% gegen EXXY 0,46%; beide thesaurierend, damit die
    # Ausschuettung den Vergleich nicht ueberlagert). Ohne TER waeren beide
    # gleich - und der guenstige Baustein damit relativ zu schlecht dargestellt.
    guenstig = simulate(_ter_rows("IBCI", 53), _ter_strategie("IBCI", fondskosten=True))
    teuer = simulate(_ter_rows("EXXY", 53), _ter_strategie("EXXY", fondskosten=True))

    assert guenstig.value_history[-1].total_value > teuer.value_history[-1].total_value
