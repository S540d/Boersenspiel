from __future__ import annotations

from datetime import date

import pytest

from boersenspiel.sources import alphavantage as av


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        av.AlphaVantageSource()


def test_quote_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse({"Global Quote": {"05. price": "82.10"}}),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "ok"
    assert result["EUNL"].source == "alphavantage"
    assert result["EUNL"].price == 82.10


def test_quote_missing_data_yields_missing_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(
            {"Information": "Please consider optimizing your API request frequency."}
        ),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "missing"
    assert result["EUNL"].price is None


def test_unmapped_ticker_yields_missing_without_request(monkeypatch: pytest.MonkeyPatch):
    def _fail(*args, **kwargs):
        raise AssertionError("sollte fuer ein nicht gemapptes Symbol nicht aufgerufen werden")

    monkeypatch.setattr(av.requests, "get", _fail)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["UNKNOWN"], date(2026, 8, 24))

    assert result["UNKNOWN"].status == "missing"


def test_crypto_success_prefers_eur_column(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Time Series (Digital Currency Daily)": {
            "2026-08-24": {
                "4a. close (USD)": "70000.00",
                "4b. close (EUR)": "58000.00",
            },
            "2026-08-23": {
                "4a. close (USD)": "69000.00",
                "4b. close (EUR)": "57500.00",
            },
        }
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["BTC-EUR"], date(2026, 8, 24))

    assert result["BTC-EUR"].status == "ok"
    assert result["BTC-EUR"].price == 58000.00


def test_crypto_flat_format(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Time Series (Digital Currency Daily)": {
            "2026-08-24": {"1. open": "57000", "4. close": "58000"},
        }
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["BTC-EUR"], date(2026, 8, 24))

    assert result["BTC-EUR"].status == "ok"
    assert result["BTC-EUR"].price == 58000.0


def test_request_exception_yields_missing(monkeypatch: pytest.MonkeyPatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(av.requests, "get", _raise)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "missing"
