"""Reine Simulationsfunktion: (Kurshistorie, Strategie) -> Portfolio-/Steuerzustand.

``simulate()`` hat keinerlei I/O, liest kein ``datetime.now()`` und hält
keinen Zustand außerhalb ihres eigenen Aufrufs - alles wird bei jeder
Neuerzeugung komplett aus der übergebenen Kurshistorie und der übergebenen
Strategie-Definition neu berechnet. Das ist die Grundlage für das
Abnahmekriterium "Determinismus": identische Eingaben -> identisches Ergebnis.

Modellierungsentscheidungen (siehe README für Details):
- Initialkauf: Ordergebühren werden VOM STARTKAPITAL VOR der Aufteilung
  abgezogen (dokumentierte Vorgabe aus der Planung).
- Spätere Trades (Rebalancing, Dezember-Harvest): Gebühren mindern beim
  Verkauf den realisierten Gewinn und werden beim Kauf der Kostenbasis
  zugeschlagen (Standard-Transaktionskostenbehandlung).
- Kostenbasis je Instrument wird nach der Durchschnittskosten-Methode geführt
  (kein FIFO/LIFO mit einzelnen Lots).
- Rebalancing bringt bei Auslösung ALLE Instrumente auf ihr Zielgewicht
  zurück (nicht nur den auslösenden Topf).
- Dezember-Harvest: an der letzten Kurszeile jedes Kalenderjahres werden
  verlustbehaftete Positionen (größter Verlust zuerst, bei Gleichstand nach
  Ticker sortiert) vollständig verkauft und sofort zum selben Kurs neu
  gekauft, bis der noch nicht genutzte Sparerpauschbetrag des laufenden
  Jahres durch realisierte Verluste gedeckt ist (oder keine Verlustpositionen
  mehr vorhanden sind).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .history_store import PriceRow
from .strategies import ORDERGEBUEHR, SPARERPAUSCHBETRAG_PRO_JAHR, STEUERSATZ, Strategy


@dataclass
class Trade:
    date: date
    ticker: str
    side: str  # "buy" oder "sell"
    units: Decimal
    price: Decimal
    fee: Decimal
    realized_gain: Decimal | None
    reason: str  # "initial_buy" | "rebalance" | "december_harvest" | "december_harvest_rebuy"


@dataclass
class ValuePoint:
    date: date
    total_value: Decimal
    ticker_values: dict[str, Decimal]
    ticker_weights: dict[str, Decimal]
    topf_values: dict[str, Decimal]
    topf_weights: dict[str, Decimal]


@dataclass
class TaxStatus:
    year: int
    freibetrag_verbraucht: Decimal
    freibetrag_verbleibend: Decimal
    verlustvortrag: Decimal
    kumulierte_steuer: Decimal


@dataclass
class SimulationResult:
    strategy_name: str
    value_history: list[ValuePoint]
    trades: list[Trade]
    holdings: dict[str, Decimal]
    tax_status: TaxStatus
    last_rebalance_date: date | None
    last_harvest_date: date | None


@dataclass
class _Position:
    units: Decimal = field(default_factory=lambda: Decimal(0))
    cost_total: Decimal = field(default_factory=lambda: Decimal(0))

    def avg_cost(self) -> Decimal:
        if self.units == 0:
            return Decimal(0)
        return self.cost_total / self.units


def simulate(price_history: list[PriceRow], strategy: Strategy) -> SimulationResult:
    if not price_history:
        raise ValueError("Kurshistorie ist leer - Simulation benötigt mindestens eine Zeile")

    rows = sorted(price_history, key=lambda r: r.date)
    weights = strategy.alle_ticker_gewichte()
    tickers = sorted(weights.keys())

    positions: dict[str, _Position] = {t: _Position() for t in tickers}
    trades: list[Trade] = []
    last_rebalance_date: date | None = None
    last_harvest_date: date | None = None

    # Steuerledger-Zustand (lokal, kein persistenter Zustand außerhalb dieses Aufrufs)
    current_tax_year = rows[0].date.year
    freibetrag_verbleibend = SPARERPAUSCHBETRAG_PRO_JAHR
    verlustvortrag = Decimal(0)
    kumulierte_steuer = Decimal(0)

    def process_realized_gain(gain: Decimal) -> None:
        nonlocal freibetrag_verbleibend, verlustvortrag, kumulierte_steuer
        if gain <= 0:
            verlustvortrag += -gain
            return
        offset_verlust = min(gain, verlustvortrag)
        verlustvortrag -= offset_verlust
        rest = gain - offset_verlust
        offset_freibetrag = min(rest, freibetrag_verbleibend)
        freibetrag_verbleibend -= offset_freibetrag
        steuerpflichtig = rest - offset_freibetrag
        kumulierte_steuer += steuerpflichtig * STEUERSATZ

    def reset_year_if_needed(year: int) -> None:
        nonlocal current_tax_year, freibetrag_verbleibend
        if year != current_tax_year:
            current_tax_year = year
            freibetrag_verbleibend = SPARERPAUSCHBETRAG_PRO_JAHR

    def current_values(prices: dict[str, Decimal]) -> dict[str, Decimal]:
        return {t: positions[t].units * prices[t] for t in tickers if t in prices}

    def rebalance_to_targets(
        prices: dict[str, Decimal], trade_date: date, reason: str
    ) -> bool:
        values = current_values(prices)
        total_value = sum(values.values(), Decimal(0))
        if total_value <= 0:
            return False
        diffs = {t: weights[t] * total_value - values.get(t, Decimal(0)) for t in tickers}
        executed = False
        for t in tickers:
            diff = diffs[t]
            price = prices.get(t)
            if price is None or diff == 0:
                continue
            pos = positions[t]
            if diff < 0:
                sell_value = -diff
                units_to_sell = min(sell_value / price, pos.units)
                if units_to_sell <= 0:
                    continue
                proceeds = units_to_sell * price
                cost_removed = pos.avg_cost() * units_to_sell
                realized_gain = proceeds - cost_removed - ORDERGEBUEHR
                pos.units -= units_to_sell
                pos.cost_total -= cost_removed
                trades.append(
                    Trade(trade_date, t, "sell", units_to_sell, price, ORDERGEBUEHR, realized_gain, reason)
                )
                process_realized_gain(realized_gain)
                executed = True
            else:
                buy_value = diff
                units_to_buy = buy_value / price
                pos.units += units_to_buy
                pos.cost_total += buy_value + ORDERGEBUEHR
                trades.append(
                    Trade(trade_date, t, "buy", units_to_buy, price, ORDERGEBUEHR, None, reason)
                )
                executed = True
        return executed

    def december_harvest(prices: dict[str, Decimal], trade_date: date) -> bool:
        losers: list[tuple[str, Decimal]] = []
        for t in tickers:
            price = prices.get(t)
            pos = positions[t]
            if price is None or pos.units <= 0:
                continue
            unrealized = pos.units * price - pos.cost_total
            if unrealized < 0:
                losers.append((t, -unrealized))
        if not losers:
            return False

        losers.sort(key=lambda kv: (-kv[1], kv[0]))
        harvested = Decimal(0)
        target_harvest = freibetrag_verbleibend
        executed = False
        for t, loss in losers:
            if harvested >= target_harvest:
                break
            price = prices[t]
            pos = positions[t]
            units_to_sell = pos.units
            proceeds = units_to_sell * price
            cost_removed = pos.cost_total
            realized_gain = proceeds - cost_removed - ORDERGEBUEHR
            pos.units = Decimal(0)
            pos.cost_total = Decimal(0)
            trades.append(
                Trade(trade_date, t, "sell", units_to_sell, price, ORDERGEBUEHR, realized_gain, "december_harvest")
            )
            process_realized_gain(realized_gain)
            harvested += loss
            executed = True

            # sofortiger Rückkauf zum selben Kurs, um die Marktexponierung zu erhalten
            pos.units = units_to_sell
            pos.cost_total = proceeds + ORDERGEBUEHR
            trades.append(
                Trade(trade_date, t, "buy", units_to_sell, price, ORDERGEBUEHR, None, "december_harvest_rebuy")
            )
        return executed

    def year_last_row_dates(all_rows: list[PriceRow]) -> set[date]:
        last_by_year: dict[int, date] = {}
        for r in all_rows:
            last_by_year[r.date.year] = r.date
        return set(last_by_year.values())

    harvest_dates = year_last_row_dates(rows)

    value_history: list[ValuePoint] = []

    for i, row in enumerate(rows):
        prices = row.prices
        reset_year_if_needed(row.date.year)

        if i == 0:
            num_instruments = len(tickers)
            total_fees = ORDERGEBUEHR * num_instruments
            investable = strategy.startkapital - total_fees
            for t in tickers:
                price = prices.get(t)
                if price is None:
                    continue
                buy_value = investable * weights[t]
                units = buy_value / price
                positions[t].units = units
                positions[t].cost_total = buy_value
                trades.append(Trade(row.date, t, "buy", units, price, ORDERGEBUEHR, None, "initial_buy"))
        else:
            values = current_values(prices)
            total_value = sum(values.values(), Decimal(0))
            if total_value > 0:
                ziel_topf = next(t for t in strategy.toepfe if t.name == strategy.ziel_topf)
                ziel_topf_value = sum(
                    (values.get(t, Decimal(0)) for t in ziel_topf.sub_gewichte), Decimal(0)
                )
                ist_gewicht = ziel_topf_value / total_value
                abweichung_pp = abs(ist_gewicht - strategy.ziel_gewicht) * 100
                if abweichung_pp > strategy.rebalancing_schwelle_pp:
                    if rebalance_to_targets(prices, row.date, "rebalance"):
                        last_rebalance_date = row.date

        if row.date in harvest_dates:
            if december_harvest(prices, row.date):
                last_harvest_date = row.date

        values = current_values(prices)
        total_value = sum(values.values(), Decimal(0))
        ticker_weights = {
            t: (values.get(t, Decimal(0)) / total_value if total_value > 0 else Decimal(0)) for t in tickers
        }
        topf_values = {
            topf.name: sum((values.get(t, Decimal(0)) for t in topf.sub_gewichte), Decimal(0))
            for topf in strategy.toepfe
        }
        topf_weights = {
            name: (val / total_value if total_value > 0 else Decimal(0)) for name, val in topf_values.items()
        }
        value_history.append(
            ValuePoint(row.date, total_value, values, ticker_weights, topf_values, topf_weights)
        )

    tax_status = TaxStatus(
        year=current_tax_year,
        freibetrag_verbraucht=SPARERPAUSCHBETRAG_PRO_JAHR - freibetrag_verbleibend,
        freibetrag_verbleibend=freibetrag_verbleibend,
        verlustvortrag=verlustvortrag,
        kumulierte_steuer=kumulierte_steuer,
    )
    holdings = {t: positions[t].units for t in tickers}

    return SimulationResult(
        strategy_name=strategy.name,
        value_history=value_history,
        trades=trades,
        holdings=holdings,
        tax_status=tax_status,
        last_rebalance_date=last_rebalance_date,
        last_harvest_date=last_harvest_date,
    )
