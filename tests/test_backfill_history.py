"""Tests für den einmaligen historischen Backfill (``scripts/backfill_history.py``).

Reine Logik-Tests ohne echten Netzwerkzugriff: ``collect_weekly_series`` wird
gegen ein Fake-``AlphaVantageSource``-Objekt getestet (keine HTTP-Mocks
nötig, da die Methode nur die öffentlichen ``fetch_*``-Methoden aufruft),
``write_backfilled_history`` gegen ``tmp_path`` als Daten-Verzeichnis.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backfill_history as bh  # noqa: E402

from boersenspiel.history_store import read_price_history  # noqa: E402
from boersenspiel.instruments import TICKERS  # noqa: E402


class _FakeSource:
    def __init__(self, weekly: dict[str, dict[date, float]], fx: dict[date, float], crypto: dict[date, float]):
        self._weekly = weekly
        self._fx = fx
        self._crypto = crypto
        self.weekly_history_calls: list[str] = []

    def fetch_weekly_history(self, ticker: str, since: date) -> dict[date, float]:
        self.weekly_history_calls.append(ticker)
        return {d: p for d, p in self._weekly.get(ticker, {}).items() if d >= since}

    def fetch_fx_weekly_eur_per_usd(self, since: date) -> dict[date, float]:
        return {d: r for d, r in self._fx.items() if d >= since}

    def fetch_crypto_weekly_history(self, since: date) -> dict[date, float]:
        return {d: p for d, p in self._crypto.items() if d >= since}


def test_collect_weekly_series_converts_usd_tickers_to_eur():
    source = _FakeSource(
        weekly={
            "EUNL": {date(2026, 8, 14): 85.0},
            "LITE": {date(2026, 8, 14): 100.0},
        },
        fx={date(2026, 8, 14): 0.90},
        crypto={date(2026, 8, 14): 58000.0},
    )
    result = bh.collect_weekly_series(source, ["EUNL", "LITE", "BTC-EUR"], since=date(2020, 1, 1))

    assert result["EUNL"][date(2026, 8, 14)] == 85.0  # EUR-Ticker unveraendert
    assert result["LITE"][date(2026, 8, 14)] == pytest.approx(90.0)  # 100 USD * 0.90
    assert result["BTC-EUR"][date(2026, 8, 14)] == 58000.0


def test_collect_weekly_series_skips_fx_fetch_when_no_usd_tickers():
    source = _FakeSource(weekly={"EUNL": {date(2026, 8, 14): 85.0}}, fx={}, crypto={})

    def _fail(*args, **kwargs):
        raise AssertionError("FX_WEEKLY sollte ohne USD-Ticker nicht abgerufen werden")

    source.fetch_fx_weekly_eur_per_usd = _fail
    result = bh.collect_weekly_series(source, ["EUNL"], since=date(2020, 1, 1))

    assert result["EUNL"][date(2026, 8, 14)] == 85.0


def test_collect_weekly_series_forward_fills_fx_rate_for_missing_week():
    # FX-Kurs existiert nur fuer eine frueher Woche - die USD-Aktie hat aber
    # auch eine spaetere Woche, fuer die es (noch) keinen neueren FX-Kurs gibt.
    source = _FakeSource(
        weekly={"LITE": {date(2026, 8, 14): 100.0, date(2026, 8, 21): 110.0}},
        fx={date(2026, 8, 14): 0.90},
        crypto={},
    )
    result = bh.collect_weekly_series(source, ["LITE"], since=date(2020, 1, 1))

    assert result["LITE"][date(2026, 8, 14)] == pytest.approx(90.0)
    assert result["LITE"][date(2026, 8, 21)] == pytest.approx(99.0)  # Forward-Fill des 0.90-Kurses


def test_write_backfilled_history_writes_rows_and_carries_forward_missing_tickers(tmp_path: Path):
    per_ticker = {
        "EUNL": {date(2026, 8, 14): 85.0, date(2026, 8, 21): 87.0},
        "EUNA": {date(2026, 8, 14): 5.0},  # fehlt in der zweiten Woche -> carry forward erwartet
    }
    week_count = bh.write_backfilled_history(per_ticker, tmp_path)

    assert week_count == 2
    rows = read_price_history(tmp_path)
    assert len(rows) == 2
    assert rows[0].date == date(2026, 8, 14)
    assert rows[0].prices["EUNL"] == Decimal("85.0")
    assert rows[0].prices["EUNA"] == Decimal("5.0")
    assert rows[1].prices["EUNL"] == Decimal("87.0")
    assert rows[1].prices["EUNA"] == Decimal("5.0")  # carried forward


def test_write_backfilled_history_resets_existing_files(tmp_path: Path):
    (tmp_path / "price_history.csv").write_text("Date,OLDTICKER\n2000-01-01,1\n")

    per_ticker = {"EUNL": {date(2026, 8, 14): 85.0}}
    bh.write_backfilled_history(per_ticker, tmp_path)

    rows = read_price_history(tmp_path)
    assert len(rows) == 1
    assert rows[0].date == date(2026, 8, 14)


def test_write_backfilled_history_groups_by_iso_week_using_latest_date():
    # Zwei Ticker mit leicht unterschiedlichem "letzten Handelstag" (Donnerstag
    # vs. Freitag) derselben ISO-Kalenderwoche sollen in DIESELBE Zeile fallen.
    per_ticker = {
        "EUNL": {date(2026, 8, 14): 85.0},  # Freitag, KW 33/2026
        "EUNA": {date(2026, 8, 13): 5.0},  # Donnerstag, dieselbe ISO-Woche
    }
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        week_count = bh.write_backfilled_history(per_ticker, Path(tmp))
        rows = read_price_history(Path(tmp))

    assert week_count == 1
    assert len(rows) == 1
    assert rows[0].date == date(2026, 8, 14)  # spaeteres Datum der Woche gewinnt
    assert rows[0].prices["EUNA"] == Decimal("5.0")


def test_all_tickers_have_weekly_fetch_support():
    """Regressionsschutz: Jeder Ticker aus instruments.py muss entweder ueber
    fetch_weekly_history (Symbol-Mapping vorhanden) oder als BTC-EUR ueber den
    Krypto-Pfad im Backfill abgedeckt sein."""
    from boersenspiel.sources.alphavantage import ALPHAVANTAGE_SYMBOLS

    fehlend = [t for t in TICKERS if t != "BTC-EUR" and t not in ALPHAVANTAGE_SYMBOLS]
    assert fehlend == []


def test_fx_is_fetched_before_any_ticker_to_limit_the_cost_of_a_failure():
    """Beim ersten echten Lauf scheiterte FX_WEEKLY erst NACH 16 Ticker-
    Requests - bei 25 Requests/Tag war damit auch der zweite Versuch fuer
    denselben Tag verloren. Der FX-Abruf muss deshalb zuerst laufen: dann
    kostet derselbe Fehlschlag genau einen Request."""
    reihenfolge: list[str] = []

    class _ProtokollierendeSource(_FakeSource):
        def fetch_weekly_history(self, ticker: str, since: date) -> dict[date, float]:
            reihenfolge.append(f"ticker:{ticker}")
            return super().fetch_weekly_history(ticker, since)

        def fetch_fx_weekly_eur_per_usd(self, since: date) -> dict[date, float]:
            reihenfolge.append("fx")
            return super().fetch_fx_weekly_eur_per_usd(since)

        def fetch_crypto_weekly_history(self, since: date) -> dict[date, float]:
            reihenfolge.append("crypto")
            return super().fetch_crypto_weekly_history(since)

    source = _ProtokollierendeSource(
        weekly={"EUNL": {date(2026, 8, 14): 85.0}, "LITE": {date(2026, 8, 14): 100.0}},
        fx={date(2026, 8, 14): 0.90},
        crypto={date(2026, 8, 14): 58000.0},
    )
    bh.collect_weekly_series(source, ["EUNL", "LITE", "BTC-EUR"], since=date(2020, 1, 1))

    assert reihenfolge[0] == "fx"
    assert reihenfolge[-1] == "crypto"


def test_a_failing_fx_fetch_costs_no_ticker_requests():
    source = _FakeSource(
        weekly={"LITE": {date(2026, 8, 14): 100.0}}, fx={date(2026, 8, 14): 0.90}, crypto={}
    )

    def _rate_limit(*args, **kwargs):
        raise RuntimeError("keine Zeitreihe in der Antwort fuer USD/EUR (FX_WEEKLY)")

    source.fetch_fx_weekly_eur_per_usd = _rate_limit
    with pytest.raises(RuntimeError):
        bh.collect_weekly_series(source, ["LITE", "BTC-EUR"], since=date(2020, 1, 1))

    assert source.weekly_history_calls == []
