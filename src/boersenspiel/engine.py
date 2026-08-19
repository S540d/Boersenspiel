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
- Dezember-Harvest: an der letzten Kurszeile eines ABGESCHLOSSENEN
  Kalenderjahres (ein späteres Jahr ist in der Historie bereits vertreten,
  oder die Zeile liegt selbst im Dezember) greift genau eine von zwei sich
  gegenseitig ausschließenden Maßnahmen, je nachdem wie das Steuerjahr bis
  dahin gelaufen ist (siehe #13/#16):
  (A) Freibetrag-Gewinnmitnahme, wenn der Sparerpauschbetrag des Jahres noch
      nicht ausgeschöpft ist (`freibetrag_verbleibend > 0`): Gewinnpositionen
      (größter unrealisierter Gewinn zuerst) werden anteilig verkauft und
      sofort zum selben Kurs zurückgekauft, bis der realisierte Gewinn den
      verbleibenden Freibetrag genau ausschöpft, nicht überschreitet. Der
      Gewinn bleibt steuerfrei, die Kostenbasis wird steuerfrei angehoben.
  (B) Echtes Tax-Loss-Harvesting, wenn im Jahr bereits ein steuerpflichtiger
      Gewinn realisiert wurde (Freibetrag ist folglich bereits 0):
      Verlustpositionen (größter unrealisierter Verlust zuerst) werden
      anteilig verkauft und sofort zurückgekauft, bis die realisierten
      Verluste den steuerpflichtigen Teil der Jahresgewinne decken. Das
      verschiebt die bereits versteuerte Gewinnsumme nicht rückwirkend,
      sondern baut einen Verlustvortrag auf, der künftige Gewinne mindert.
  Bei Gleichstand wird nach Ticker sortiert. Das laufende, noch
  unvollständige Jahr löst keinen Harvest aus, auch wenn seine bislang
  letzte Zeile zufällig die neueste der gesamten Historie ist.
- Fehlt einem Instrument in der ersten Kurszeile der Kurs (z. B. ein Titel,
  der erst später an die Börse ging), bleibt sein Kapitalanteil als
  unverzinste Cash-Position geparkt, statt verlorenzugehen - sobald die
  erste Kurszeile mit diesem Instrument erscheint, wird die Cash-Position
  vollständig investiert. Fehlt der Kurs in einer SPÄTEREN Zeile für ein
  bereits gehaltenes Instrument, wird die Position mit dem letzten bekannten
  Kurs bewertet statt aus der Summe zu fallen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .history_store import PriceRow
from .strategies import ORDERGEBUEHR, SPARERPAUSCHBETRAG_PRO_JAHR, STEUERSATZ, Optimierungen, Strategy


@dataclass
class Trade:
    date: date
    ticker: str
    side: str  # "buy" oder "sell"
    units: Decimal
    price: Decimal
    fee: Decimal
    realized_gain: Decimal | None
    reason: str  # "initial_buy" | "delayed_initial_buy" | "rebalance" | "freibetrag_gewinnmitnahme" | "freibetrag_gewinnmitnahme_rebuy" | "tax_loss_harvest" | "tax_loss_harvest_rebuy"


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


def simulate(
    price_history: list[PriceRow],
    strategy: Strategy,
    optimierungen: Optimierungen | None = None,
) -> SimulationResult:
    if not price_history:
        raise ValueError("Kurshistorie ist leer - Simulation benötigt mindestens eine Zeile")

    opt = optimierungen if optimierungen is not None else strategy.optimierungen
    gebuehr = ORDERGEBUEHR if opt.ordergebuehren else Decimal(0)

    rows = sorted(price_history, key=lambda r: r.date)
    base_weights = strategy.alle_ticker_gewichte()
    tickers = sorted(base_weights.keys())

    def weights_at(i: int) -> dict[str, Decimal]:
        """Ziel-Gewichte für Zeile ``i``: dynamisch via ``gewichte_fn`` (z. B.
        saisonale/charttechnische Szenarien) oder sonst die konstanten
        Barbell-Gewichte, unverändert wie bisher."""
        if strategy.gewichte_fn is not None:
            return strategy.gewichte_fn(rows, i)
        return base_weights

    positions: dict[str, _Position] = {t: _Position() for t in tickers}
    # Kapitalanteil eines Instruments ohne Kurs in der ersten Zeile (z. B. vor
    # dessen Börsengang) - wird investiert, sobald erstmals ein Kurs vorliegt.
    pending_cash: dict[str, Decimal] = {t: Decimal(0) for t in tickers}
    # Letzter bekannter Kurs je Instrument - haelt eine Position bewertbar,
    # wenn eine spaetere Zeile fuer dieses Instrument keinen Kurs liefert.
    last_price: dict[str, Decimal] = {}
    trades: list[Trade] = []
    last_rebalance_date: date | None = None
    last_harvest_date: date | None = None

    # Steuerledger-Zustand (lokal, kein persistenter Zustand außerhalb dieses Aufrufs)
    current_tax_year = rows[0].date.year
    freibetrag_verbleibend = SPARERPAUSCHBETRAG_PRO_JAHR
    verlustvortrag = Decimal(0)
    kumulierte_steuer = Decimal(0)
    # Steuerpflichtiger (bereits versteuerter) Anteil der im laufenden Jahr
    # realisierten Gewinne - Trigger und Zielgröße für den Tax-Loss-Harvest
    # (Maßnahme B), unabhängig von freibetrag_verbleibend.
    steuerpflichtige_gewinne_jahr = Decimal(0)

    def process_realized_gain(gain: Decimal) -> None:
        nonlocal freibetrag_verbleibend, verlustvortrag, kumulierte_steuer, steuerpflichtige_gewinne_jahr
        if not opt.besteuerung:
            return
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
        steuerpflichtige_gewinne_jahr += steuerpflichtig

    def reset_year_if_needed(year: int) -> None:
        nonlocal current_tax_year, freibetrag_verbleibend, steuerpflichtige_gewinne_jahr
        if year != current_tax_year:
            current_tax_year = year
            freibetrag_verbleibend = SPARERPAUSCHBETRAG_PRO_JAHR
            steuerpflichtige_gewinne_jahr = Decimal(0)

    def total_cash() -> Decimal:
        return sum(pending_cash.values(), Decimal(0))

    def current_values(prices: dict[str, Decimal]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for t in tickers:
            price = prices.get(t, last_price.get(t))
            if price is None:
                continue
            values[t] = positions[t].units * price
        return values

    def rebalance_to_targets(
        prices: dict[str, Decimal], trade_date: date, reason: str, weights: dict[str, Decimal]
    ) -> bool:
        values = current_values(prices)
        total_value = sum(values.values(), Decimal(0)) + total_cash()
        if total_value <= 0:
            return False
        diffs = {
            t: weights.get(t, Decimal(0)) * total_value - values.get(t, Decimal(0)) for t in tickers
        }
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
                realized_gain = proceeds - cost_removed - gebuehr
                pos.units -= units_to_sell
                pos.cost_total -= cost_removed
                trades.append(
                    Trade(trade_date, t, "sell", units_to_sell, price, gebuehr, realized_gain, reason)
                )
                process_realized_gain(realized_gain)
                executed = True
            else:
                buy_value = diff
                units_to_buy = buy_value / price
                pos.units += units_to_buy
                pos.cost_total += buy_value + gebuehr
                trades.append(
                    Trade(trade_date, t, "buy", units_to_buy, price, gebuehr, None, reason)
                )
                executed = True
        return executed

    def december_gewinnmitnahme(prices: dict[str, Decimal], trade_date: date) -> bool:
        """Maßnahme A: Gewinnpositionen anteilig verkaufen und sofort
        zurückkaufen, bis der realisierte Gewinn den verbleibenden
        Freibetrag genau ausschöpft (nicht überschreitet)."""
        winners: list[tuple[str, Decimal]] = []
        for t in tickers:
            price = prices.get(t)
            pos = positions[t]
            if price is None or pos.units <= 0:
                continue
            unrealized = pos.units * price - pos.cost_total
            if unrealized > 0:
                winners.append((t, unrealized))
        if not winners:
            return False

        winners.sort(key=lambda kv: (-kv[1], kv[0]))
        executed = False
        for t, _ in winners:
            ziel_gewinn = freibetrag_verbleibend
            if ziel_gewinn <= 0:
                break
            price = prices[t]
            pos = positions[t]
            avg_cost = pos.avg_cost()
            gewinn_je_stueck = price - avg_cost
            if gewinn_je_stueck <= 0:
                continue
            units_to_sell = min((ziel_gewinn + gebuehr) / gewinn_je_stueck, pos.units)
            if units_to_sell <= 0:
                continue
            proceeds = units_to_sell * price
            cost_removed = avg_cost * units_to_sell
            realized_gain = proceeds - cost_removed - gebuehr
            pos.units -= units_to_sell
            pos.cost_total -= cost_removed
            trades.append(
                Trade(trade_date, t, "sell", units_to_sell, price, gebuehr, realized_gain, "freibetrag_gewinnmitnahme")
            )
            process_realized_gain(realized_gain)
            executed = True

            # sofortiger Rückkauf zum selben Kurs, um die Marktexponierung zu
            # erhalten - hebt die Kostenbasis steuerfrei an.
            pos.units += units_to_sell
            pos.cost_total += proceeds + gebuehr
            trades.append(
                Trade(trade_date, t, "buy", units_to_sell, price, gebuehr, None, "freibetrag_gewinnmitnahme_rebuy")
            )
        return executed

    def december_tax_loss_harvest(prices: dict[str, Decimal], trade_date: date) -> bool:
        """Maßnahme B: Verlustpositionen anteilig verkaufen und sofort
        zurückkaufen, bis die realisierten Verluste den bereits
        steuerpflichtig gewordenen Teil der Jahresgewinne decken."""
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
        ziel_verlust = steuerpflichtige_gewinne_jahr
        executed = False
        for t, _ in losers:
            restziel = ziel_verlust - harvested
            if restziel <= 0:
                break
            price = prices[t]
            pos = positions[t]
            avg_cost = pos.avg_cost()
            verlust_je_stueck = avg_cost - price
            if verlust_je_stueck <= 0:
                continue
            units_needed = max(restziel - gebuehr, Decimal(0)) / verlust_je_stueck
            if units_needed <= 0:
                # Restziel liegt unter der Gebühr allein - jeder Verkauf
                # würde das Ziel überschießen, also hier abbrechen statt
                # überzuharvesten.
                break
            units_to_sell = min(units_needed, pos.units)
            proceeds = units_to_sell * price
            cost_removed = avg_cost * units_to_sell
            realized_gain = proceeds - cost_removed - gebuehr
            pos.units -= units_to_sell
            pos.cost_total -= cost_removed
            trades.append(
                Trade(trade_date, t, "sell", units_to_sell, price, gebuehr, realized_gain, "tax_loss_harvest")
            )
            process_realized_gain(realized_gain)
            harvested += -realized_gain
            executed = True

            # sofortiger Rückkauf zum selben Kurs, um die Marktexponierung zu erhalten
            pos.units += units_to_sell
            pos.cost_total += proceeds + gebuehr
            trades.append(
                Trade(trade_date, t, "buy", units_to_sell, price, gebuehr, None, "tax_loss_harvest_rebuy")
            )
        return executed

    def year_last_row_dates(all_rows: list[PriceRow]) -> set[date]:
        """Harvest-Zeitpunkte: die letzte Kurszeile eines ABGESCHLOSSENEN
        Kalenderjahres - also eines Jahres, dem in der Historie ein späteres
        Jahr folgt, oder dessen letzte Zeile selbst im Dezember liegt. Ohne
        diese Einschränkung wäre die jeweils neueste Zeile der Historie immer
        eine Harvest-Zeile, egal welcher Monat gerade ist, weil sie zwangsläufig
        die letzte ihres (noch laufenden) Jahres ist."""
        last_by_year: dict[int, date] = {}
        for r in all_rows:
            last_by_year[r.date.year] = r.date
        max_year = max(last_by_year)
        return {d for y, d in last_by_year.items() if y < max_year or d.month == 12}

    harvest_dates = year_last_row_dates(rows)

    value_history: list[ValuePoint] = []

    for i, row in enumerate(rows):
        prices = row.prices
        reset_year_if_needed(row.date.year)
        for t in tickers:
            if t in prices:
                last_price[t] = prices[t]

        if i == 0:
            initial_weights = weights_at(0)
            num_instruments = len(tickers)
            total_fees = gebuehr * num_instruments
            investable = strategy.startkapital - total_fees
            for t in tickers:
                price = prices.get(t)
                buy_value = investable * initial_weights.get(t, Decimal(0))
                if price is None:
                    # Instrument noch ohne Kurs (z. B. vor seinem Börsengang) -
                    # Kapitalanteil bleibt als Cash geparkt, statt zu verfallen.
                    pending_cash[t] = buy_value
                    continue
                units = buy_value / price
                positions[t].units = units
                positions[t].cost_total = buy_value
                trades.append(Trade(row.date, t, "buy", units, price, gebuehr, None, "initial_buy"))
        else:
            for t in tickers:
                if pending_cash[t] > 0 and t in prices:
                    price = prices[t]
                    buy_value = pending_cash[t]
                    units = buy_value / price
                    positions[t].units += units
                    positions[t].cost_total += buy_value
                    trades.append(
                        Trade(row.date, t, "buy", units, price, Decimal(0), None, "delayed_initial_buy")
                    )
                    pending_cash[t] = Decimal(0)

            values = current_values(prices)
            total_value = sum(values.values(), Decimal(0)) + total_cash()
            if total_value > 0:
                ziel_topf = next(t for t in strategy.toepfe if t.name == strategy.ziel_topf)
                if strategy.gewichte_fn is not None:
                    current_weights = strategy.gewichte_fn(rows, i)
                    ziel_gewicht_effektiv = sum(
                        (current_weights.get(t, Decimal(0)) for t in ziel_topf.sub_gewichte), Decimal(0)
                    )
                else:
                    current_weights = base_weights
                    ziel_gewicht_effektiv = strategy.ziel_gewicht
                ziel_topf_value = sum(
                    (values.get(t, Decimal(0)) for t in ziel_topf.sub_gewichte), Decimal(0)
                )
                ist_gewicht = ziel_topf_value / total_value
                abweichung_pp = abs(ist_gewicht - ziel_gewicht_effektiv) * 100
                if opt.rebalancing and abweichung_pp > strategy.rebalancing_schwelle_pp:
                    if rebalance_to_targets(prices, row.date, "rebalance", current_weights):
                        last_rebalance_date = row.date

        if opt.steueroptimierung and row.date in harvest_dates:
            if freibetrag_verbleibend > 0:
                if december_gewinnmitnahme(prices, row.date):
                    last_harvest_date = row.date
            elif steuerpflichtige_gewinne_jahr > 0:
                if december_tax_loss_harvest(prices, row.date):
                    last_harvest_date = row.date

        values = current_values(prices)
        total_value = sum(values.values(), Decimal(0)) + total_cash()
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
