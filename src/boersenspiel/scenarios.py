"""Auswertungs-Szenarien: zeitabhängige Handelsregeln statt konstanter Barbell-Gewichte.

Jedes Szenario ist eine ganz normale ``Strategy`` (aus ``strategies.py``), nur mit
gesetztem ``gewichte_fn`` - die Engine (``engine.py``) kennt keinerlei Details dieser
Regeln, sie ruft ``gewichte_fn(rows, i)`` pro Kurszeile auf und rebalanciert wie gehabt
auf die zurückgegebenen Ziel-Gewichte. Toepfe/Sub-Gewichte der Instrumente bleiben
strukturell die der Barbell-Strategie (Topf A "Sicherheit", Topf B "Wachstum") - die
Szenarien verschieben nur, WANN und WIE STARK zwischen (und innerhalb) beider Töpfe
umgeschichtet wird. Jedes ``gewichte_fn`` liest ausschließlich ``rows[:i+1]`` (kein
Lookahead-Bias).

Dies ist ein erster Ansatz (bewusst einfache Regeln, keine Optimierung/Backtesting der
Parameter) für drei Kategorien:

1. Börsenweisheiten: "Sell in May and Go Away", "Buy & Hold" (Gegenbeispiel: gar keine
   taktische Umschichtung), "Jahresendrallye" (Santa-Claus-Rally), "Antizyklisch kaufen"
   (Buy the Dip) und "Verluste begrenzen" (Trailing-Stop je Wachstums-Instrument).
2. Charttechnik: gleitender-Durchschnitt-Crossover (Golden Cross / Death Cross) auf dem
   MSCI-World-ETF als Trendindikator fürs Gesamtdepot.
3. Weitere Ansätze: Momentum-/Relative-Stärke-Rotation zwischen den Wachstums-
   Instrumenten, volatilitätsbasierte Aktienquote, Cost-Average-Einstieg statt
   Einmalanlage.
"""

from __future__ import annotations

from decimal import Decimal

from .history_store import PriceRow
from .strategies import BARBELL_20_80, Strategy, Topf

TOPF_SICHERHEIT: Topf = BARBELL_20_80.toepfe[0]
TOPF_WACHSTUM: Topf = BARBELL_20_80.toepfe[1]
GROWTH_TICKER: list[str] = list(TOPF_WACHSTUM.sub_gewichte.keys())
TREND_TICKER = "EUNL"  # MSCI-World-ETF als Proxy fuer den breiten Markttrend

_NORMAL_GEWICHTE: dict[str, Decimal] = BARBELL_20_80.alle_ticker_gewichte()

# Ziel-Gewichte, wenn eine Regel "defensiv" auslöst: 100% Topf A (Sicherheit),
# Topf B (Wachstum) komplett auf 0 - keine neuen Töpfe/Instrumente, nur eine
# andere Verteilung der bestehenden.
_DEFENSIV_GEWICHTE: dict[str, Decimal] = {
    **TOPF_SICHERHEIT.sub_gewichte,
    **{t: Decimal(0) for t in TOPF_WACHSTUM.sub_gewichte},
}

# Ziel-Gewichte, wenn eine Regel "aggressiv" auslöst: Wachstum auf 95% hochgefahren,
# Sicherheit auf 5% reduziert (Sub-Gewichte innerhalb der Töpfe bleiben unverändert).
_AGGRESSIVE_WACHSTUMSQUOTE = Decimal("0.95")
_AGGRESSIV_GEWICHTE: dict[str, Decimal] = {
    **{t: g * (Decimal(1) - _AGGRESSIVE_WACHSTUMSQUOTE) for t, g in TOPF_SICHERHEIT.sub_gewichte.items()},
    **{t: g * _AGGRESSIVE_WACHSTUMSQUOTE for t, g in TOPF_WACHSTUM.sub_gewichte.items()},
}


def _rolling_prices(rows: list[PriceRow], i: int, ticker: str, fenster_wochen: int) -> list[Decimal] | None:
    """Kurse der letzten ``fenster_wochen`` Zeilen (inkl. Zeile ``i``), oder ``None``
    falls noch nicht genug Historie vorhanden oder ein Kurs fehlt."""
    start = i - fenster_wochen + 1
    if start < 0:
        return None
    kurse = [rows[j].prices.get(ticker) for j in range(start, i + 1)]
    if any(k is None for k in kurse):
        return None
    return kurse  # type: ignore[return-value]


def _sma(rows: list[PriceRow], i: int, ticker: str, fenster_wochen: int) -> Decimal | None:
    kurse = _rolling_prices(rows, i, ticker, fenster_wochen)
    if kurse is None:
        return None
    return sum(kurse, Decimal(0)) / Decimal(fenster_wochen)


# =====================================================================================
# 1. Börsenweisheiten
# =====================================================================================

# --- 1a: "Sell in May and Go Away" ---------------------------------------------------
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


# --- 1b: "Buy & Hold" -----------------------------------------------------------------
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


# --- 1c: "Jahresendrallye" (Santa-Claus-Rally) -----------------------------------------
#
# Börsenweisheit: die Aktienmärkte tendieren zum Jahreswechsel (Dezember bis
# Anfang Januar) überdurchschnittlich stark zu steigen ("Santa Claus Rally").
# In diesem Fenster wird die Wachstumsquote von 80% auf 95% hochgefahren,
# sonst gilt die normale Barbell-Verteilung.

_JAHRESENDRALLYE_MONATE = {12, 1}


def santa_claus_rally_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    monat = rows[i].date.month
    if monat in _JAHRESENDRALLYE_MONATE:
        return _AGGRESSIV_GEWICHTE
    return _NORMAL_GEWICHTE


SANTA_CLAUS_RALLY = Strategy(
    name="Börsenweisheit: Jahresendrallye",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=santa_claus_rally_gewichte,
)


# --- 1d: "Antizyklisch kaufen" (Buy the Dip) -------------------------------------------
#
# Börsenweisheit ("kaufe, wenn die Kanonen donnern" / Rothschild): nach einem
# deutlichen Kursrückgang antizyklisch stärker investieren statt zu verkaufen.
# Liegt der MSCI-World-ETF mehr als BUY_THE_DIP_SCHWELLE unter seinem
# gleitenden Höchststand der letzten BUY_THE_DIP_FENSTER_WOCHEN Wochen, wird
# die Wachstumsquote auf 95% hochgefahren, sonst gilt die normale Verteilung.

BUY_THE_DIP_FENSTER_WOCHEN = 20
BUY_THE_DIP_SCHWELLE = Decimal("0.10")  # 10% Rückgang vom Rolling-Hoch


def buy_the_dip_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    kurse = _rolling_prices(rows, i, TREND_TICKER, BUY_THE_DIP_FENSTER_WOCHEN)
    if kurse is None:
        return _NORMAL_GEWICHTE
    rolling_hoch = max(kurse)
    aktueller_kurs = kurse[-1]
    if rolling_hoch <= 0:
        return _NORMAL_GEWICHTE
    ruckgang = (rolling_hoch - aktueller_kurs) / rolling_hoch
    if ruckgang > BUY_THE_DIP_SCHWELLE:
        return _AGGRESSIV_GEWICHTE
    return _NORMAL_GEWICHTE


BUY_THE_DIP = Strategy(
    name="Börsenweisheit: Antizyklisch kaufen",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=buy_the_dip_gewichte,
)


# --- 1e: "Verluste begrenzen" (Trailing-Stop je Wachstums-Instrument) ------------------
#
# Börsenweisheit "cut your losses short": fällt ein einzelnes Wachstums-
# Instrument mehr als CUT_LOSSES_SCHWELLE unter sein Rolling-Hoch der letzten
# CUT_LOSSES_FENSTER_WOCHEN Wochen, wird NUR dieses Instrument auf 0% gesetzt -
# das freiwerdende Gewicht fließt anteilig in den Sicherheits-Topf. Andere
# Wachstums-Instrumente bleiben unangetastet (im Gegensatz zu den
# marktweiten Szenarien oben, die immer das ganze Depot umschichten).

CUT_LOSSES_FENSTER_WOCHEN = 20
CUT_LOSSES_SCHWELLE = Decimal("0.15")  # 15% Rückgang vom eigenen Rolling-Hoch


def cut_losses_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    gewichte = dict(_NORMAL_GEWICHTE)
    freigewordenes_gewicht = Decimal(0)
    for ticker in GROWTH_TICKER:
        kurse = _rolling_prices(rows, i, ticker, CUT_LOSSES_FENSTER_WOCHEN)
        if kurse is None:
            continue
        rolling_hoch = max(kurse)
        aktueller_kurs = kurse[-1]
        if rolling_hoch <= 0:
            continue
        ruckgang = (rolling_hoch - aktueller_kurs) / rolling_hoch
        if ruckgang > CUT_LOSSES_SCHWELLE:
            freigewordenes_gewicht += gewichte[ticker]
            gewichte[ticker] = Decimal(0)
    if freigewordenes_gewicht > 0:
        for ticker, sub_gewicht in TOPF_SICHERHEIT.sub_gewichte.items():
            gewichte[ticker] = gewichte.get(ticker, Decimal(0)) + freigewordenes_gewicht * sub_gewicht
    return gewichte


CUT_LOSSES = Strategy(
    name="Börsenweisheit: Verluste begrenzen",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=cut_losses_gewichte,
)


# =====================================================================================
# 2. Charttechnik
# =====================================================================================

# Golden Cross / Death Cross auf dem MSCI-World-ETF (EUNL) als Proxy für den
# breiten Markttrend: kurzer SMA unter langem SMA ("Death Cross") -> defensiv
# (100% Topf A), sonst ("Golden Cross"/normal) -> reguläre Barbell-Gewichte.
# Fensterlängen sind an wöchentliche Kursdaten angepasst (10/40 Wochen statt
# der bei Tagesdaten üblichen 50/200 Tage).

SMA_KURZ_WOCHEN = 10
SMA_LANG_WOCHEN = 40


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


# =====================================================================================
# 3. Weitere Ansätze
# =====================================================================================

# --- 3a: Momentum-/Relative-Stärke-Rotation --------------------------------------------
#
# Innerhalb des Wachstums-Topfs (Gesamtgewicht bleibt bei 80%) werden nur die
# MOMENTUM_TOP_N Instrumente mit der höchsten Trailing-Rendite der letzten
# MOMENTUM_FENSTER_WOCHEN Wochen gleichgewichtet gehalten, die übrigen auf 0%
# gesetzt. Der Sicherheits-Topf bleibt unverändert bei den normalen Barbell-
# Gewichten.

MOMENTUM_FENSTER_WOCHEN = 12
MOMENTUM_TOP_N = 2


def _trailing_return(rows: list[PriceRow], i: int, ticker: str, fenster_wochen: int) -> Decimal | None:
    start = i - fenster_wochen
    if start < 0:
        return None
    kurs_start = rows[start].prices.get(ticker)
    kurs_aktuell = rows[i].prices.get(ticker)
    if kurs_start is None or kurs_aktuell is None or kurs_start == 0:
        return None
    return (kurs_aktuell - kurs_start) / kurs_start


def momentum_rotation_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    renditen = {}
    for ticker in GROWTH_TICKER:
        rendite = _trailing_return(rows, i, ticker, MOMENTUM_FENSTER_WOCHEN)
        if rendite is not None:
            renditen[ticker] = rendite
    if len(renditen) < MOMENTUM_TOP_N:
        # Noch nicht genug Historie fuer alle Instrumente -> regulaer investiert.
        return _NORMAL_GEWICHTE

    rangliste = sorted(renditen.items(), key=lambda kv: kv[1], reverse=True)
    top_ticker = {ticker for ticker, _ in rangliste[:MOMENTUM_TOP_N]}
    anteil_je_top_ticker = TOPF_WACHSTUM.gewicht_gesamt / Decimal(len(top_ticker))

    gewichte = dict(_NORMAL_GEWICHTE)  # Sicherheits-Topf unveraendert
    for ticker in GROWTH_TICKER:
        gewichte[ticker] = anteil_je_top_ticker if ticker in top_ticker else Decimal(0)
    return gewichte


MOMENTUM_ROTATION = Strategy(
    name="Momentum: Relative-Stärke-Rotation",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=momentum_rotation_gewichte,
)


# --- 3b: Volatilitätsbasierte Aktienquote ----------------------------------------------
#
# Die Wachstumsquote (normal 80%) wird abhängig von der realisierten Volatilität
# (Standardabweichung der wöchentlichen Renditen der letzten
# VOLATILITAET_FENSTER_WOCHEN Wochen) des MSCI-World-ETF linear zwischen
# WACHSTUMSQUOTE_MIN (bei hoher Volatilität) und WACHSTUMSQUOTE_MAX (bei
# niedriger Volatilität) skaliert - klassisches Risk-Parity-/Vol-Targeting-Prinzip.

VOLATILITAET_FENSTER_WOCHEN = 12
VOLATILITAET_NIEDRIG = Decimal("0.015")  # 1.5% woechentliche Standardabweichung
VOLATILITAET_HOCH = Decimal("0.05")  # 5% woechentliche Standardabweichung
WACHSTUMSQUOTE_MAX = Decimal("0.90")
WACHSTUMSQUOTE_MIN = Decimal("0.50")


def _weekly_returns(rows: list[PriceRow], i: int, ticker: str, fenster_wochen: int) -> list[Decimal] | None:
    kurse = _rolling_prices(rows, i, ticker, fenster_wochen + 1)
    if kurse is None:
        return None
    return [(kurse[j] - kurse[j - 1]) / kurse[j - 1] for j in range(1, len(kurse)) if kurse[j - 1] != 0]


def _volatility(rendite: list[Decimal]) -> Decimal | None:
    n = len(rendite)
    if n == 0:
        return None
    mittelwert = sum(rendite, Decimal(0)) / Decimal(n)
    varianz = sum(((r - mittelwert) ** 2 for r in rendite), Decimal(0)) / Decimal(n)
    return varianz.sqrt()


def volatility_target_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    rendite = _weekly_returns(rows, i, TREND_TICKER, VOLATILITAET_FENSTER_WOCHEN)
    volatilitaet = _volatility(rendite) if rendite is not None else None
    if volatilitaet is None:
        return _NORMAL_GEWICHTE

    if volatilitaet <= VOLATILITAET_NIEDRIG:
        wachstumsquote = WACHSTUMSQUOTE_MAX
    elif volatilitaet >= VOLATILITAET_HOCH:
        wachstumsquote = WACHSTUMSQUOTE_MIN
    else:
        anteil = (volatilitaet - VOLATILITAET_NIEDRIG) / (VOLATILITAET_HOCH - VOLATILITAET_NIEDRIG)
        wachstumsquote = WACHSTUMSQUOTE_MAX - anteil * (WACHSTUMSQUOTE_MAX - WACHSTUMSQUOTE_MIN)
    sicherheitsquote = Decimal(1) - wachstumsquote

    gewichte = {t: sub * sicherheitsquote for t, sub in TOPF_SICHERHEIT.sub_gewichte.items()}
    gewichte.update({t: sub * wachstumsquote for t, sub in TOPF_WACHSTUM.sub_gewichte.items()})
    return gewichte


VOLATILITY_TARGET = Strategy(
    name="Volatilitätsbasierte Aktienquote",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("5"),
    gewichte_fn=volatility_target_gewichte,
)


# --- 3c: Cost-Average-Einstieg (statt Einmalanlage) -------------------------------------
#
# Statt das Startkapital sofort komplett zu investieren, wird die Wachstumsquote
# über die ersten COST_AVERAGE_FENSTER_WOCHEN Wochen linear von 0% (alles im
# Sicherheits-Topf "geparkt") auf die normale Barbell-Verteilung hochgefahren -
# eine Annäherung an ratierliches Investieren statt einer Einmalanlage
# ("Cost-Average-Effekt"). Ab Woche COST_AVERAGE_FENSTER_WOCHEN gilt die normale
# Verteilung dauerhaft.

COST_AVERAGE_FENSTER_WOCHEN = 10


def cost_average_gewichte(rows: list[PriceRow], i: int) -> dict[str, Decimal]:
    anteil = min(Decimal(i) / Decimal(COST_AVERAGE_FENSTER_WOCHEN), Decimal(1))
    if anteil >= 1:
        return _NORMAL_GEWICHTE
    return {
        t: _DEFENSIV_GEWICHTE.get(t, Decimal(0)) * (Decimal(1) - anteil) + _NORMAL_GEWICHTE.get(t, Decimal(0)) * anteil
        for t in _NORMAL_GEWICHTE
    }


COST_AVERAGE_ENTRY = Strategy(
    name="Cost-Average-Einstieg (10 Wochen)",
    startkapital=Decimal("10000"),
    toepfe=BARBELL_20_80.toepfe,
    ziel_topf=BARBELL_20_80.ziel_topf,
    ziel_gewicht=BARBELL_20_80.ziel_gewicht,
    rebalancing_schwelle_pp=Decimal("3"),
    gewichte_fn=cost_average_gewichte,
)


SCENARIOS: list[Strategy] = [
    SELL_IN_MAY,
    BUY_AND_HOLD,
    SANTA_CLAUS_RALLY,
    BUY_THE_DIP,
    CUT_LOSSES,
    CHART_SMA_CROSSOVER,
    MOMENTUM_ROTATION,
    VOLATILITY_TARGET,
    COST_AVERAGE_ENTRY,
]

SCENARIOS_BY_NAME: dict[str, Strategy] = {s.name: s for s in SCENARIOS}
