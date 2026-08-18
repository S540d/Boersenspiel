from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from boersenspiel.history_store import read_price_history, record_week, row_date_from_quotes
from boersenspiel.sources import PriceQuote


def _quotes(**prices: float) -> dict[str, PriceQuote]:
    return {t: PriceQuote(ticker=t, price=p, status="ok", source="test") for t, p in prices.items()}


def test_record_week_appends_new_row(tmp_path: Path):
    record_week(date(2024, 1, 1), _quotes(EUNL=80.0, EUNA=5.0), data_dir=tmp_path)
    rows = read_price_history(tmp_path)
    assert len(rows) == 1
    assert rows[0].date == date(2024, 1, 1)
    assert rows[0].prices["EUNL"] == Decimal("80.0")


def test_record_week_updates_same_iso_week_instead_of_duplicating(tmp_path: Path):
    # Montag und Mittwoch derselben ISO-Kalenderwoche
    record_week(date(2024, 1, 1), _quotes(EUNL=80.0), data_dir=tmp_path)
    record_week(date(2024, 1, 3), _quotes(EUNL=82.0), data_dir=tmp_path)

    rows = read_price_history(tmp_path)
    assert len(rows) == 1
    assert rows[0].date == date(2024, 1, 3)
    assert rows[0].prices["EUNL"] == Decimal("82.0")


def test_record_week_new_iso_week_appends_second_row(tmp_path: Path):
    record_week(date(2024, 1, 1), _quotes(EUNL=80.0), data_dir=tmp_path)
    record_week(date(2024, 1, 8), _quotes(EUNL=81.0), data_dir=tmp_path)

    rows = read_price_history(tmp_path)
    assert len(rows) == 2


def test_record_week_carries_forward_missing_price(tmp_path: Path):
    record_week(date(2024, 1, 1), _quotes(EUNL=80.0, EUNA=5.0), data_dir=tmp_path)

    missing_quote = {"EUNL": PriceQuote("EUNL", None, "missing", "test")}
    record_week(date(2024, 1, 8), missing_quote, data_dir=tmp_path)

    rows = read_price_history(tmp_path)
    assert len(rows) == 2
    assert rows[1].prices["EUNL"] == Decimal("80.0")

    log_content = (tmp_path / "fetch_log.csv").read_text()
    assert "carried_forward" in log_content


def test_record_week_missing_with_no_history_logs_missing(tmp_path: Path):
    missing_quote = {"EUNL": PriceQuote("EUNL", None, "missing", "test")}
    record_week(date(2024, 1, 1), missing_quote, data_dir=tmp_path)

    log_content = (tmp_path / "fetch_log.csv").read_text()
    assert "missing" in log_content

    rows = read_price_history(tmp_path)
    assert "EUNL" not in rows[0].prices


def test_carry_forward_uses_previous_week_not_a_later_one(tmp_path: Path):
    """Wird eine Luecke nachtraeglich gefuellt, darf der Carry-Forward nur auf
    zurueckliegende Wochen zurueckgreifen - sonst landet der Kurs einer
    spaeteren Woche (Blick in die Zukunft) in der Historie."""
    record_week(date(2024, 1, 1), _quotes(EUNL=100.0), data_dir=tmp_path)
    record_week(date(2024, 1, 15), _quotes(EUNL=200.0), data_dir=tmp_path)

    missing_quote = {"EUNL": PriceQuote("EUNL", None, "missing", "test")}
    row = record_week(date(2024, 1, 8), missing_quote, data_dir=tmp_path)

    assert row.prices["EUNL"] == Decimal("100.0")

    rows = read_price_history(tmp_path)
    assert [r.prices["EUNL"] for r in rows] == [Decimal("100.0"), Decimal("100.0"), Decimal("200.0")]


def _quote(ticker: str, price: float, quote_date: date | None) -> PriceQuote:
    return PriceQuote(ticker=ticker, price=price, status="ok", source="test", quote_date=quote_date)


def test_row_date_uses_the_reported_trading_day_not_the_request_day():
    """Montagslauf, Kurse vom Freitag -> die Zeile gehoert in die Freitags-Woche."""
    quotes = {
        "EUNL": _quote("EUNL", 80.0, date(2026, 8, 21)),
        "EUNA": _quote("EUNA", 5.0, date(2026, 8, 21)),
    }
    assert row_date_from_quotes(quotes, fallback=date(2026, 8, 24)) == date(2026, 8, 21)


def test_row_date_picks_the_most_common_trading_day():
    """BTC-EUR handelt 24/7 und meldet einen spaeteren Tag als die Boersen -
    ein einzelner Ausreisser darf die ganze Zeile nicht verschieben."""
    quotes = {
        "EUNL": _quote("EUNL", 80.0, date(2026, 8, 21)),
        "EUNA": _quote("EUNA", 5.0, date(2026, 8, 21)),
        "BTC-EUR": _quote("BTC-EUR", 55000.0, date(2026, 8, 23)),
    }
    assert row_date_from_quotes(quotes, fallback=date(2026, 8, 24)) == date(2026, 8, 21)


def test_row_date_breaks_ties_towards_the_earlier_day():
    quotes = {
        "EUNL": _quote("EUNL", 80.0, date(2026, 8, 21)),
        "BTC-EUR": _quote("BTC-EUR", 55000.0, date(2026, 8, 23)),
    }
    assert row_date_from_quotes(quotes, fallback=date(2026, 8, 24)) == date(2026, 8, 21)


def test_row_date_ignores_failed_quotes():
    quotes = {
        "EUNL": _quote("EUNL", 80.0, date(2026, 8, 21)),
        "EUNA": PriceQuote("EUNA", None, "missing", "test", quote_date=date(2026, 1, 1)),
    }
    assert row_date_from_quotes(quotes, fallback=date(2026, 8, 24)) == date(2026, 8, 21)


def test_row_date_falls_back_when_no_source_reports_a_trading_day():
    """Der manuelle Weg (record_prices.py) liefert keinen Handelstag -
    dort bleibt das uebergebene Datum massgeblich."""
    quotes = {"EUNL": _quote("EUNL", 80.0, None)}
    assert row_date_from_quotes(quotes, fallback=date(2026, 8, 24)) == date(2026, 8, 24)
