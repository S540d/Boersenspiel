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
        lambda url, params, timeout: _FakeResponse({"Global Quote": {}}),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "missing"
    assert result["EUNL"].price is None


def test_quote_rate_limit_response_yields_rate_limited_status(monkeypatch: pytest.MonkeyPatch):
    """Ein erreichtes Tageslimit liefert HTTP 200 mit "Information" statt
    Kursdaten - das darf nicht wie eine echte Kurslücke aussehen (Issue #11)."""
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(
            {"Information": "Please consider optimizing your API request frequency."}
        ),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "rate_limited"
    assert result["EUNL"].price is None


def test_quote_note_response_yields_rate_limited_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse({"Note": "our standard API rate limit is 25 requests per day"}),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "rate_limited"


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


def test_crypto_rate_limit_response_yields_rate_limited_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse({"Note": "our standard API rate limit is 25 requests per day"}),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["BTC-EUR"], date(2026, 8, 24))

    assert result["BTC-EUR"].status == "rate_limited"


def test_request_exception_yields_missing(monkeypatch: pytest.MonkeyPatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(av.requests, "get", _raise)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "missing"


# --- USD-Ticker: EUR/USD-Umrechnung (Waehrungskonsistenz-Fix) ------------------


def _dispatch_by_function(responses: dict[str, dict]):
    def _get(url, params, timeout):
        return _FakeResponse(responses[params["function"]])

    return _get


def test_usd_ticker_is_converted_to_eur(monkeypatch: pytest.MonkeyPatch):
    responses = {
        "CURRENCY_EXCHANGE_RATE": {"Realtime Currency Exchange Rate": {"5. Exchange Rate": "0.90"}},
        "GLOBAL_QUOTE": {"Global Quote": {"05. price": "100.00"}},
    }
    monkeypatch.setattr(av.requests, "get", _dispatch_by_function(responses))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["LITE"], date(2026, 8, 24))

    assert result["LITE"].status == "ok"
    assert result["LITE"].price == pytest.approx(90.0)


def test_usd_eur_rate_fetched_only_once_for_multiple_usd_tickers(monkeypatch: pytest.MonkeyPatch):
    call_count = {"CURRENCY_EXCHANGE_RATE": 0, "GLOBAL_QUOTE": 0}

    def _get(url, params, timeout):
        call_count[params["function"]] += 1
        if params["function"] == "CURRENCY_EXCHANGE_RATE":
            return _FakeResponse({"Realtime Currency Exchange Rate": {"5. Exchange Rate": "0.90"}})
        return _FakeResponse({"Global Quote": {"05. price": "100.00"}})

    monkeypatch.setattr(av.requests, "get", _get)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["LITE", "TSLA"], date(2026, 8, 24))

    assert call_count["CURRENCY_EXCHANGE_RATE"] == 1
    assert call_count["GLOBAL_QUOTE"] == 2
    assert result["LITE"].price == pytest.approx(90.0)
    assert result["TSLA"].price == pytest.approx(90.0)


def test_eur_ticker_is_not_converted(monkeypatch: pytest.MonkeyPatch):
    def _fail_on_fx(url, params, timeout):
        if params["function"] == "CURRENCY_EXCHANGE_RATE":
            raise AssertionError("EUR-Ticker sollte keinen EUR/USD-Kurs abrufen")
        return _FakeResponse({"Global Quote": {"05. price": "82.10"}})

    monkeypatch.setattr(av.requests, "get", _fail_on_fx)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].price == 82.10


def test_usd_ticker_yields_missing_when_fx_rate_unavailable(monkeypatch: pytest.MonkeyPatch):
    def _get(url, params, timeout):
        if params["function"] == "CURRENCY_EXCHANGE_RATE":
            return _FakeResponse({"Information": "rate limit"})
        raise AssertionError("sollte ohne EUR/USD-Kurs keinen Kurs mehr abrufen")

    monkeypatch.setattr(av.requests, "get", _get)
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["LITE"], date(2026, 8, 24))

    assert result["LITE"].status == "missing"
    assert result["LITE"].price is None


# --- Historische Wochenkurse (fuer scripts/backfill_history.py) ---------------


def test_fetch_weekly_history_filters_by_since_date(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Weekly Time Series": {
            "2026-08-14": {"4. close": "90.00"},
            "2020-01-03": {"4. close": "50.00"},
        }
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch_weekly_history("EUNL", since=date(2024, 1, 1))

    assert result == {date(2026, 8, 14): 90.0}


def test_fetch_weekly_history_raises_for_unmapped_ticker():
    source = av.AlphaVantageSource(api_key="dummy")
    with pytest.raises(ValueError):
        source.fetch_weekly_history("UNKNOWN", since=date(2020, 1, 1))


def test_fetch_weekly_history_raises_when_series_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse({"Information": "..."}))
    source = av.AlphaVantageSource(api_key="dummy")
    with pytest.raises(RuntimeError):
        source.fetch_weekly_history("EUNL", since=date(2020, 1, 1))


def test_fetch_fx_weekly_eur_per_usd(monkeypatch: pytest.MonkeyPatch):
    # Schluesselname und Feldnamen exakt so, wie die echte API sie liefert
    # (belegt durch den Backfill-Lauf vom 18.08.2026) - der erste Lauf
    # scheiterte, weil hier "Weekly Time Series (FX)" angenommen wurde.
    payload = {
        "Meta Data": {
            "1. Information": "Forex Weekly Prices (open, high, low, close)",
            "2. From Symbol": "USD",
            "3. To Symbol": "EUR",
        },
        "Time Series FX (Weekly)": {
            "2026-08-14": {"1. open": "0.9100", "4. close": "0.9200"},
            "2019-01-04": {"1. open": "0.8600", "4. close": "0.8700"},
        },
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch_fx_weekly_eur_per_usd(since=date(2024, 1, 1))

    assert result == {date(2026, 8, 14): 0.92}


def test_fetch_crypto_weekly_history_prefers_usd_column(monkeypatch: pytest.MonkeyPatch):
    """market=USD statt market=EUR (Issue #56: EUR liefert im Free-Tier nur
    ca. 50 statt der vollen Historie) - die Spaltenwahl bevorzugt deshalb
    jetzt die USD- statt der EUR-Spalte im alten, zweispaltigen Format."""
    payload = {
        "Time Series (Digital Currency Weekly)": {
            "2026-08-14": {"4a. close (USD)": "70000.00", "4b. close (EUR)": "58000.00"},
            "2015-01-04": {"4a. close (USD)": "300.00", "4b. close (EUR)": "250.00"},
        }
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch_crypto_weekly_history(since=date(2024, 1, 1))

    assert result == {date(2026, 8, 14): 70000.0}


def test_fetch_crypto_weekly_history_flat_format(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Time Series (Digital Currency Weekly)": {
            "2026-08-14": {"4. close": "58000.00"},
        }
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch_crypto_weekly_history(since=date(2020, 1, 1))

    assert result == {date(2026, 8, 14): 58000.0}


def test_fetch_crypto_weekly_history_requests_usd_market(monkeypatch: pytest.MonkeyPatch):
    """Issue #56: market=EUR liefert im Free-Tier nur ca. 50 statt der vollen
    Historie zurueck - der Request muss deshalb market=USD anfragen."""
    seen_params: dict = {}

    def fake_get(url, params, timeout):
        seen_params.update(params)
        return _FakeResponse({"Time Series (Digital Currency Weekly)": {"2026-08-14": {"4. close": "58000.00"}}})

    monkeypatch.setattr(av.requests, "get", fake_get)
    source = av.AlphaVantageSource(api_key="dummy")
    source.fetch_crypto_weekly_history(since=date(2020, 1, 1))

    assert seen_params["market"] == "USD"


def test_quote_reports_trading_day_not_request_day(monkeypatch: pytest.MonkeyPatch):
    """Ein Montagslauf liefert den Freitagsschluss - der Handelstag aus
    "07. latest trading day" muss durchgereicht werden, sonst landet der Kurs
    eine Woche zu spaet in der Historie (siehe row_date_from_quotes)."""
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(
            {"Global Quote": {"05. price": "82.10", "07. latest trading day": "2026-08-21"}}
        ),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))  # Montag

    assert result["EUNL"].quote_date == date(2026, 8, 21)  # Freitag


def test_quote_without_trading_day_field_stays_usable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse({"Global Quote": {"05. price": "82.10"}}),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "ok"
    assert result["EUNL"].quote_date is None


def test_unparsable_trading_day_does_not_break_the_quote(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        av.requests,
        "get",
        lambda url, params, timeout: _FakeResponse(
            {"Global Quote": {"05. price": "82.10", "07. latest trading day": "keinDatum"}}
        ),
    )
    source = av.AlphaVantageSource(api_key="dummy")
    result = source.fetch(["EUNL"], date(2026, 8, 24))

    assert result["EUNL"].status == "ok"
    assert result["EUNL"].price == 82.10
    assert result["EUNL"].quote_date is None


# --- Zeitreihen-Extraktion -----------------------------------------------------
#
# Alpha Vantage benennt den Zeitreihen-Schluessel je Endpunkt anders. Die
# folgenden Payloads sind die real beobachteten Formate: TIME_SERIES_WEEKLY aus
# dem erfolgreichen Teil des Backfill-Laufs vom 18.08.2026, FX_WEEKLY aus dessen
# Fehlermeldung, DIGITAL_CURRENCY_WEEKLY aus einem direkten Abruf.


def test_weekly_history_accepts_the_real_time_series_key(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Meta Data": {"1. Information": "Weekly Prices", "2. Symbol": "EUNL.DEX"},
        "Weekly Time Series": {
            "2026-08-14": {"1. open": "127.0", "4. close": "128.70"},
            "2019-01-04": {"1. open": "70.0", "4. close": "71.00"},
        },
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")

    assert source.fetch_weekly_history("EUNL", since=date(2024, 1, 1)) == {date(2026, 8, 14): 128.70}


def test_crypto_weekly_accepts_the_real_time_series_key(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "Meta Data": {"1. Information": "Weekly Prices and Volumes for Digital Currency"},
        "Time Series (Digital Currency Weekly)": {
            "2026-08-18": {"1. open": "54293.03", "4. close": "55555.27", "5. volume": "230.17"},
        },
    }
    monkeypatch.setattr(av.requests, "get", lambda url, params, timeout: _FakeResponse(payload))
    source = av.AlphaVantageSource(api_key="dummy")

    assert source.fetch_crypto_weekly_history(since=date(2024, 1, 1)) == {date(2026, 8, 18): 55555.27}


def test_rate_limit_response_raises_instead_of_looking_like_an_empty_series():
    """Rate-Limit-/Fehlerantworten haben nur String-Werte - sie duerfen nicht
    als leere Zeitreihe durchgehen, sondern muessen den Backfill abbrechen."""
    payload = {"Information": "our standard API rate limit is 25 requests per day"}
    with pytest.raises(RuntimeError, match="keine Zeitreihe"):
        av._extract_time_series(payload, "TESTKONTEXT")


def test_extraction_is_independent_of_the_series_key_name():
    """Kern des Fixes: der Schluesselname wird nicht mehr geraten."""
    for key in ("Weekly Time Series", "Time Series FX (Weekly)", "Time Series (Digital Currency Weekly)"):
        payload = {"Meta Data": {"1. Information": "..."}, key: {"2026-08-14": {"4. close": "1.0"}}}
        assert av._extract_time_series(payload, "TESTKONTEXT") == {"2026-08-14": {"4. close": "1.0"}}


def test_ambiguous_response_raises_rather_than_guessing():
    payload = {
        "Meta Data": {"1. Information": "..."},
        "Weekly Time Series": {"2026-08-14": {"4. close": "1.0"}},
        "Monthly Time Series": {"2026-08-31": {"4. close": "2.0"}},
    }
    with pytest.raises(RuntimeError, match="mehrdeutig"):
        av._extract_time_series(payload, "TESTKONTEXT")


def test_error_message_is_truncated_for_huge_payloads():
    """Eine komplette Kurshistorie im Traceback macht das Actions-Log unlesbar."""
    payload = {"Information": "x" * 5000}
    with pytest.raises(RuntimeError) as exc:
        av._extract_time_series(payload, "TESTKONTEXT")
    assert "gekuerzt" in str(exc.value)
    assert len(str(exc.value)) < 1000
