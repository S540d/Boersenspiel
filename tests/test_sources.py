from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from boersenspiel.sources import yfinance_stooq as ys


class _FakeTicker:
    def __init__(self, history_df: pd.DataFrame | None):
        self._history_df = history_df

    def history(self, period: str):
        if self._history_df is None:
            raise RuntimeError("simulated yfinance failure")
        return self._history_df


def test_yfinance_success(monkeypatch: pytest.MonkeyPatch):
    df = pd.DataFrame({"Close": [79.5, 80.25]})
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        type("M", (), {"Ticker": staticmethod(lambda symbol: _FakeTicker(df))}),
    )

    source = ys.YfinanceStooqSource()
    result = source.fetch(["EUNL"], date(2024, 1, 1))

    assert result["EUNL"].status == "ok"
    assert result["EUNL"].source == "yfinance"
    assert result["EUNL"].price == 80.25


def test_yfinance_failure_falls_back_to_stooq(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        type("M", (), {"Ticker": staticmethod(lambda symbol: _FakeTicker(None))}),
    )

    stooq_csv = "Date,Open,High,Low,Close,Volume\n2024-01-01,80,81,79,80.5,1000\n"

    class _FakeResponse:
        text = stooq_csv

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(ys.requests, "get", lambda url, timeout: _FakeResponse())

    source = ys.YfinanceStooqSource()
    result = source.fetch(["EUNL"], date(2024, 1, 1))

    assert result["EUNL"].status == "ok"
    assert result["EUNL"].source == "stooq"
    assert result["EUNL"].price == 80.5


def test_both_sources_fail_yields_missing_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        type("M", (), {"Ticker": staticmethod(lambda symbol: _FakeTicker(None))}),
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(ys.requests, "get", _raise)

    source = ys.YfinanceStooqSource()
    result = source.fetch(["EUNL"], date(2024, 1, 1))

    assert result["EUNL"].status == "missing"
    assert result["EUNL"].price is None
