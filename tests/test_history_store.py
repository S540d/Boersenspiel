from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from boersenspiel.history_store import read_price_history, record_week
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
