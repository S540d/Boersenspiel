"""Auswertungs-Szenarien: zeitabhängige Handelsregeln statt konstanter Barbell-Gewichte.

Jedes Szenario ist eine ganz normale ``Strategy`` (aus ``strategies.py``), nur mit
gesetztem ``gewichte_fn`` - die Engine (``engine.py``) kennt keinerlei Details dieser
Regeln, sie ruft ``gewichte_fn(rows, i)`` pro Kurszeile auf und rebalanciert wie gehabt
auf die zurückgegebenen Ziel-Gewichte. Toepfe/Sub-Gewichte der Instrumente bleiben
strukturell die der Barbell-Strategie (Topf A "Sicherheit", Topf B "Wachstum") - die
Szenarien verschieben nur, WANN und WIE STARK zwischen beiden Töpfen umgeschichtet wird.

Dies ist ein erster Ansatz (bewusst einfache Regeln, keine Optimierung/Backtesting der
Parameter) für drei Kategorien:

1. Börsenweisheiten: "Sell in May and Go Away" (saisonal) und "Buy & Hold" (Gegenbeispiel:
   gar keine taktische Umschichtung, nur halten - ebenfalls eine klassische Weisheit,
   "Hin und her macht Taschen leer").
2. Charttechnik: gleitender-Durchschnitt-Crossover (Golden Cross / Death Cross) auf dem
   MSCI-World-ETF als Trendindikator fürs Gesamtdepot.
3. Weitere denkbare Szenarien (nicht implementiert, siehe README/PR-Beschreibung):
   Momentum-/Relative-Stärke-Rotation zwischen den Wachstums-Instrumenten, volatilitätsbasierte
   Aktienquote, Cost-Average-Einstieg statt Einmalanlage.
"""

from __future__ import annotations

from decimal import Decimal

from .history_store import PriceRow
from .strategies import BARBELL_20_80, Strategy, Topf

TOPF_SICHERHEIT: Topf = BARBELL_20_80.toepfe[0]
TOPF_WACHSTUM: Topf = BARBELL_20_80.toepfe[1]

_NORMAL_GEWICHTE: dict[str, Decimal] = BARBELL_20_80.alle_ticker_gewichte()

# Ziel-Gewichte, wenn eine Regel "defensiv" auslöst: 100% Topf A (Sicherheit),
# Topf B (Wachstum) komplett auf 0 - keine neue Töpfe/Instrumente, nur eine
# andere Verteilung der bestehenden.
_DEFENSIV_GEWICHTE: dict[str, Decimal] = {
    **TOPF_SICHERHEIT.sub_gewichte,
    **{t: Decimal(0) for t in TOPF_WACHSTUM.sub_gewichte},
}


# --- Szenario 1: "Sell in May and Go Away" ----------------------------------
#
# Klassische Börsenweisheit: Aktien im Sommerhalbjahr meiden ("...and come back
# on St. Leger's Day", Mitte September) - hier vereinfacht als Mai bis
# September (inklusive) defensiv, Oktober bis April normal investiert.

_SELL_IN_MAY_MONATE = {5, 6, 7, 8, 9}


def sell_in_may_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    monat = rows[i].date.month
    if monat in _SELL_IN_MAY_MONATE:
        return _DEFENSIV_GEWICHTE
    return _NORMAL_GEWICHTE


SELL_IN_MAY = Strategy(
    name="Börsenweisheit: Sell in May",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    # Kleinere Schwelle als beim reinen Rebalancing, damit der saisonale
    # Regimewechsel (Mai <-> Oktober) zuverlässig eine Umschichtung auslöst.
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=sell_in_may_gewichte,
)


# --- Szenario 1b: "Buy & Hold" -----------------------------------------------
#
# Gegenstück zur taktischen Umschichtung: klassische Weisheit "Hin und her
# macht Taschen leer" - Anfangsallokation wird nie aktiv rebalanciert (die
# Rebalancing-Schwelle liegt oberhalb jeder erreichbaren Abweichung). Der
# Dezember-Verlustverrechnungs-Mechanismus (steuerliche Optimierung, keine
# Umschichtung der Zielgewichte) bleibt wie bei den anderen Strategien aktiv.

BUY_AND_HOLD = Strategy(
    name="Börsenweisheit: Buy & Hold",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("1000"),
    gewichte_fn=None,
)


# --- Szenario 2: Charttechnik - gleitender-Durchschnitt-Crossover -----------
#
# Golden Cross / Death Cross auf dem MSCI-World-ETF (EUNL) als Proxy für den
# breiten Markttrend: kurzer SMA unter langem SMA ("Death Cross") -> defensiv
# (100% Topf A), sonst ("Golden Cross"/normal) -> reguläre Barbell-Gewichte.
# Fensterlängen sind an wöchentliche Kursdaten angepasst (10/40 Wochen statt
# der bei Tagesdaten üblichen 50/200 Tage).

TREND_TICKER = "EUNL"
SMA_KURZ_WOCHEN = 10
SMA_LANG_WOCHEN = 40


def _sma(rows: list[PriceRow], i: int, ticker: str, fenster_wochen: int) -> Decimal | None:
    start = i - fenster_wochen + 1
    if start < 0:
        return None
    kurse = [rows[j].prices.get(ticker) for j in range(start, i + 1)]
    if any(k is None for k in kurse):
        return None
    return sum(kurse, Decimal(0)) / Decimal(fenster_wochen)


def chart_sma_crossover_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    sma_kurz = _sma(rows, i, TREND_TICKER, SMA_KURZ_WOCHEN)
    sma_lang = _sma(rows, i, TREND_TICKER, SMA_LANG_WOCHEN)
    if sma_kurz is None or sma_lang is None:
        # Noch nicht genug Historie fuer den langen SMA -> regulaer investiert.
        return _NORMAL_GEWICHTE
    if sma_kurz < sma_lang:
        return _DEFENSIV_GEWICHTE
    return _NORMAL_GEWICHTE


CHART_SMA_CROSSOVER = Strategy(
    name="Charttechnik: SMA-Crossover (10/40 Wochen)",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=chart_sma_crossover_gewichte,
)


SCENARIOS: list[Strategy] = [SELL_IN_MAY, BUY_AND_HOLD, CHART_SMA_CROSSOVER]

SCENARIOS_BY_NAME: dict[str, Strategy] = {s.name: s for s in SCENARIOS}
