"""CSV-Lese-/Schreiblogik für die Kurshistorie.

Dies ist der EINZIGE Schreibzugriff auf ``data/price_history.csv`` und
``data/fetch_log.csv``. Jede Kursquelle (automatisiert via GitHub Actions oder
manuell z. B. über Cowork/Websuche) ruft ausschließlich ``record_week`` auf -
das Kurshistorie-Schema bleibt dadurch stabil, unabhängig davon, woher die
Kurse stammen. Diese Datei kennt weder eine konkrete Kursquelle noch eine
Strategie.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from .instruments import TICKERS
from .sources import PriceQuote

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PRICE_HISTORY_FILE = "price_history.csv"
FETCH_LOG_FILE = "fetch_log.csv"

PRICE_HISTORY_HEADER = ["Date", *TICKERS]
FETCH_LOG_HEADER = ["Date", "Ticker", "Status", "Source", "Note"]


@dataclass(frozen=True)
class PriceRow:
    date: date
    prices: dict[str, Decimal]


def _price_history_path(data_dir: Path) -> Path:
    return data_dir / PRICE_HISTORY_FILE


def _fetch_log_path(data_dir: Path) -> Path:
    return data_dir / FETCH_LOG_FILE


def _ensure_files(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    hist_path = _price_history_path(data_dir)
    if not hist_path.exists():
        with hist_path.open("w", newline="") as f:
            csv.writer(f).writerow(PRICE_HISTORY_HEADER)
    log_path = _fetch_log_path(data_dir)
    if not log_path.exists():
        with log_path.open("w", newline="") as f:
            csv.writer(f).writerow(FETCH_LOG_HEADER)


def read_price_history(data_dir: Path = DEFAULT_DATA_DIR) -> list[PriceRow]:
    """Liest die komplette Kurshistorie, aufsteigend nach Datum sortiert."""
    _ensure_files(data_dir)
    rows: list[PriceRow] = []
    with _price_history_path(data_dir).open(newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row_date = date.fromisoformat(raw["Date"])
            prices = {t: Decimal(raw[t]) for t in TICKERS if raw.get(t)}
            rows.append(PriceRow(date=row_date, prices=prices))
    rows.sort(key=lambda r: r.date)
    return rows


def _iso_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]  # (ISO-Jahr, ISO-Kalenderwoche)


def row_date_from_quotes(quotes: dict[str, PriceQuote], fallback: date) -> date:
    """Datum, unter dem eine Abruf-Charge abgelegt wird: der Handelstag, auf
    den sich die Kurse beziehen - nicht der Tag des Abrufs.

    Ein Montagslauf vor Börsenbeginn liefert den Freitagsschluss der Vorwoche.
    Wird der Montag als Zeilendatum genommen, landet dieser Kurs in der
    falschen ISO-Woche - und damit eine Woche versetzt gegenüber dem
    historischen Backfill, der dieselben Kurse über ihren Handelstag
    einsortiert.

    Da die Ticker unterschiedliche Handelskalender haben (Xetra-Freitag vs.
    BTC-EUR als 24/7-Markt), wird der HÄUFIGSTE Handelstag der erfolgreichen
    Quotes genommen, bei Gleichstand der frühere. Das ist robust gegen
    einzelne Ticker mit abweichendem Feiertag und trifft bei den Börsentickern
    zuverlässig deren gemeinsamen letzten Handelstag.

    ``fallback`` (üblicherweise das Abrufdatum) greift, wenn keine einzige
    Quote einen Handelstag mitliefert - etwa beim manuellen Weg über
    ``record_prices.py``.
    """
    tage = [q.quote_date for q in quotes.values() if q.status == "ok" and q.quote_date is not None]
    if not tage:
        return fallback
    haeufigkeit = Counter(tage)
    hoechste = max(haeufigkeit.values())
    return min(d for d, anzahl in haeufigkeit.items() if anzahl == hoechste)


def record_week(
    as_of: date,
    quotes: dict[str, PriceQuote],
    data_dir: Path = DEFAULT_DATA_DIR,
) -> PriceRow:
    """Schreibt/aktualisiert die Zeile für die ISO-Kalenderwoche von ``as_of``.

    - Für Ticker mit Status "ok" wird der gelieferte Kurs übernommen.
    - Für Ticker mit Status "missing" (oder wenn ein Ticker in ``quotes``
      fehlt) wird der letzte bekannte Kurs aus der bisherigen Historie
      übernommen ("carry forward") und in fetch_log.csv vermerkt - so
      entsteht nie eine Zeile mit Lücke, solange es einen Vorwert gibt.
    - Läuft ein zweiter Abruf in derselben ISO-Kalenderwoche (z. B. ein
      manueller Re-Dispatch), wird die bestehende Zeile aktualisiert statt
      eine Dublette anzuhängen - das macht "wöchentlich" robust unabhängig
      vom genauen Wochentag des Laufs.
    """
    _ensure_files(data_dir)
    existing_rows = read_price_history(data_dir)

    target_week = _iso_week(as_of)
    same_week_index = next(
        (i for i, r in enumerate(existing_rows) if _iso_week(r.date) == target_week),
        None,
    )

    # Letzter bekannter Kurs je Ticker aus den Wochen VOR der Zielwoche.
    # Bewusst nur zurueckliegende Wochen: wird eine Luecke nachtraeglich
    # gefuellt (z. B. record_prices.py mit einem alten --date oder ein
    # Backfill, der Wochen nicht streng chronologisch schreibt), duerfte ein
    # Carry-Forward sonst den Kurs einer SPAETEREN Woche uebernehmen und damit
    # einen Blick in die Zukunft in die Historie schreiben. existing_rows ist
    # aufsteigend sortiert, das letzte update() gewinnt also.
    last_known: dict[str, Decimal] = {}
    for r in existing_rows:
        if _iso_week(r.date) >= target_week:
            continue
        last_known.update(r.prices)

    new_prices: dict[str, Decimal] = {}
    log_entries: list[list[str]] = []
    for ticker in TICKERS:
        quote = quotes.get(ticker)
        if quote is not None and quote.status == "ok" and quote.price is not None:
            new_prices[ticker] = Decimal(str(quote.price))
            continue

        source = quote.source if quote else ""
        rate_limited = quote is not None and quote.status == "rate_limited"
        if ticker in last_known:
            new_prices[ticker] = last_known[ticker]
            note = (
                "Rate-Limit der Quelle erreicht, letzter bekannter Kurs uebernommen"
                if rate_limited
                else "Kein aktueller Kurs verfuegbar, letzter bekannter Kurs uebernommen"
            )
            log_entries.append([as_of.isoformat(), ticker, "carried_forward", source, note])
        else:
            note = (
                "Rate-Limit der Quelle erreicht, kein historischer Kurs verfuegbar"
                if rate_limited
                else "Kein aktueller und kein historischer Kurs verfuegbar"
            )
            log_entries.append([as_of.isoformat(), ticker, "missing", source, note])

    if same_week_index is not None:
        existing_rows[same_week_index] = PriceRow(date=as_of, prices=new_prices)
    else:
        existing_rows.append(PriceRow(date=as_of, prices=new_prices))
    existing_rows.sort(key=lambda r: r.date)

    _write_price_history(existing_rows, data_dir)
    if log_entries:
        _append_fetch_log(log_entries, data_dir)

    return PriceRow(date=as_of, prices=new_prices)


def _write_price_history(rows: list[PriceRow], data_dir: Path) -> None:
    path = _price_history_path(data_dir)
    tmp_path = path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(PRICE_HISTORY_HEADER)
        for row in rows:
            writer.writerow(
                [row.date.isoformat()] + [str(row.prices[t]) if t in row.prices else "" for t in TICKERS]
            )
    os.replace(tmp_path, path)


def _append_fetch_log(entries: list[list[str]], data_dir: Path) -> None:
    with _fetch_log_path(data_dir).open("a", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(entries)
