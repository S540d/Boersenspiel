"""Alpha-Vantage-Kursquelle: offizielle, API-Key-basierte REST-API statt
Scraping - deutlich zuverlässiger für GitHub Actions als yfinance, das
wiederholt an Yahoos Crumb/Cookie-Authentifizierung scheiterte (siehe README).

Free-Tier-Limits: 25 Requests/Tag, max. 1 Request/Sekunde. Bei aktuell 17
Tickern (7 Barbell-Basisinstrumente + 10 Einzelaktien-Satellit, davon 9 mit
zusätzlichem EUR/USD-Kursabruf) sind das ca. 18 Requests pro Lauf - der
tägliche GitHub-Actions-Cron (`daily-update.yml`) verbraucht damit fast das
gesamte Tageslimit **jeden Tag**, es bleibt kaum noch Spielraum für
zusätzliche manuelle Abrufe am selben Tag (z. B. den historischen Backfill,
siehe unten - der sollte an einem Tag laufen, an dem der Cron pausiert oder
noch nicht gelaufen ist); zwischen den Requests wird ein kleiner Sleep
eingehalten, um das Sekundenlimit nicht zu reißen.

Läuft in GitHub Actions gegen die reine REST-API (nicht über den
Alpha-Vantage-MCP-Server, der nur innerhalb einer Claude-Session verfügbar
ist) - der API-Key kommt aus der Umgebungsvariable ``ALPHAVANTAGE_API_KEY``
(als GitHub-Actions-Secret hinterlegt).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date

import requests

from . import PriceQuote

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"

# Ticker (aus instruments.py) -> Alpha-Vantage-Symbol, per SYMBOL_SEARCH
# verifiziert. Xetra-Suffix ist ".DEX" (nicht ".DE"); EIMI ist auf Xetra
# unter dem lokalen Kuerzel "IBC3" gelistet; SEMI (iShares Global
# Semiconductors) ist auf Xetra nicht verfuegbar, nur ueber die
# Amsterdam-Notierung ".AMS" (ebenfalls in EUR). BTC-EUR laeuft ueber einen
# eigenen Krypto-Endpunkt, siehe _fetch_crypto.
#
# Einzelaktien-Satellit: US-notierte Werte (inkl. ADRs wie BYDDY/RHHBY)
# laufen direkt unter ihrem Ticker in USD (liquideste Notierung - eine
# Xetra/Frankfurt-EUR-Notierung existiert nicht fuer jeden Wert, z. B. nicht
# fuer Coca-Cola), nur SMA Solar (S92) ist analog zu den ETFs oben ueber die
# Xetra-Notierung (".DEX") angebunden und damit schon in EUR. Die restlichen
# USD-Werte werden bei jedem Abruf per aktuellem EUR/USD-Kurs umgerechnet
# (siehe USD_TICKERS/_fetch_usd_eur_rate) - die Engine kennt sonst keine
# Waehrungen und wuerde USD-Betraege sonst faelschlich als EUR behandeln.
ALPHAVANTAGE_SYMBOLS: dict[str, str] = {
    "EUNL": "EUNL.DEX",
    "EUNA": "EUNA.DEX",
    "4GLD": "4GLD.DEX",
    "LYMS": "LYMS.DEX",
    "EIMI": "IBC3.DEX",
    "SEMI": "SEMI.AMS",
    "LITE": "LITE",
    "BYDDY": "BYDDY",
    "SEDG": "SEDG",
    "S92": "S92.DEX",
    "TSLA": "TSLA",
    "PLTR": "PLTR",
    "MSTR": "MSTR",
    "RIVN": "RIVN",
    "KO": "KO",
    "RHHBY": "RHHBY",
}

# Ticker, deren Alpha-Vantage-Symbol in USD notiert - Kurs wird bei jedem
# Abruf per aktuellem EUR/USD-Kurs in EUR umgerechnet.
USD_TICKERS: frozenset[str] = frozenset({"LITE", "BYDDY", "SEDG", "TSLA", "PLTR", "MSTR", "RIVN", "KO", "RHHBY"})

_REQUEST_INTERVAL_SECONDS = 1.1


class AlphaVantageSource:
    """PriceSource-Implementierung gegen die offizielle Alpha-Vantage-REST-API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ALPHAVANTAGE_API_KEY ist nicht gesetzt - als GitHub-Actions-Secret "
                "hinterlegen (Settings -> Secrets and variables -> Actions) oder "
                "lokal als Umgebungsvariable exportieren."
            )

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, PriceQuote]:
        results: dict[str, PriceQuote] = {}
        # EUR/USD-Kurs nur einmal pro fetch()-Aufruf laden (nicht pro Ticker) -
        # gilt fuer alle USD-Ticker gemeinsam am selben Stichtag.
        usd_eur_rate: float | None = None
        usd_eur_rate_fetched = False
        is_first_request = True

        def pace() -> None:
            nonlocal is_first_request
            if not is_first_request:
                time.sleep(_REQUEST_INTERVAL_SECONDS)
            is_first_request = False

        for ticker in tickers:
            if ticker == "BTC-EUR":
                pace()
                results[ticker] = self._fetch_crypto(ticker)
            elif ticker in USD_TICKERS:
                if not usd_eur_rate_fetched:
                    pace()
                    usd_eur_rate = self._fetch_usd_eur_rate()
                    usd_eur_rate_fetched = True
                pace()
                results[ticker] = self._fetch_quote(ticker, fx_rate=usd_eur_rate)
            else:
                pace()
                results[ticker] = self._fetch_quote(ticker)
        return results

    def _fetch_usd_eur_rate(self) -> float | None:
        try:
            resp = requests.get(
                ALPHAVANTAGE_URL,
                params={
                    "function": "CURRENCY_EXCHANGE_RATE",
                    "from_currency": "USD",
                    "to_currency": "EUR",
                    "apikey": self.api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            rate_str = data.get("Realtime Currency Exchange Rate", {}).get("5. Exchange Rate")
            if not rate_str:
                print(f"alphavantage: kein EUR/USD-Kurs erhalten: {data!r}", file=sys.stderr)
                return None
            return float(rate_str)
        except Exception as exc:
            print(f"alphavantage: EUR/USD-Kursabruf fehlgeschlagen: {exc!r}", file=sys.stderr)
            return None

    def _fetch_quote(self, ticker: str, fx_rate: float | None = None) -> PriceQuote:
        symbol = ALPHAVANTAGE_SYMBOLS.get(ticker)
        if symbol is None:
            print(f"alphavantage: kein Symbol-Mapping fuer {ticker}", file=sys.stderr)
            return PriceQuote(ticker, None, "missing", "alphavantage")
        if ticker in USD_TICKERS and fx_rate is None:
            # Ohne EUR/USD-Kurs wuerde der USD-Preis faelschlich als EUR
            # gespeichert - lieber "missing" (carry-forward greift) als eine
            # falsche Waehrung in die Historie schreiben.
            print(f"alphavantage: kein EUR/USD-Kurs verfuegbar, ueberspringe {ticker}", file=sys.stderr)
            return PriceQuote(ticker, None, "missing", "alphavantage")
        try:
            resp = requests.get(
                ALPHAVANTAGE_URL,
                params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self.api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            price_str = data.get("Global Quote", {}).get("05. price")
            if not price_str:
                print(f"alphavantage: keine Kursdaten fuer {symbol}: {data!r}", file=sys.stderr)
                return PriceQuote(ticker, None, "missing", "alphavantage")
            price = float(price_str)
            if fx_rate is not None:
                price *= fx_rate
            return PriceQuote(ticker, price, "ok", "alphavantage")
        except Exception as exc:
            print(f"alphavantage fehlgeschlagen fuer {symbol}: {exc!r}", file=sys.stderr)
            return PriceQuote(ticker, None, "missing", "alphavantage")

    def _fetch_crypto(self, ticker: str) -> PriceQuote:
        try:
            resp = requests.get(
                ALPHAVANTAGE_URL,
                params={
                    "function": "DIGITAL_CURRENCY_DAILY",
                    "symbol": "BTC",
                    "market": "EUR",
                    "apikey": self.api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            series = data.get("Time Series (Digital Currency Daily)")
            if not series:
                print(f"alphavantage: keine Kryptodaten fuer BTC-EUR: {data!r}", file=sys.stderr)
                return PriceQuote(ticker, None, "missing", "alphavantage")
            latest_date = max(series.keys())
            latest = series[latest_date]
            # Alte Alpha-Vantage-Formate liefern getrennte USD/EUR-Spalten
            # (z. B. "4a. close (USD)", "4b. close (EUR)") - explizit die
            # EUR-Spalte bevorzugen, sonst auf das neuere, flache "4. close"
            # (bereits in der angefragten Marktwaehrung) zurueckfallen.
            close_key = next(
                (k for k in latest if k.startswith("4") and "EUR" in k),
                None,
            ) or next((k for k in latest if k.startswith("4.")), None)
            if close_key is None:
                print(f"alphavantage: unerwartetes Kryptoformat fuer BTC-EUR: {latest!r}", file=sys.stderr)
                return PriceQuote(ticker, None, "missing", "alphavantage")
            return PriceQuote(ticker, float(latest[close_key]), "ok", "alphavantage")
        except Exception as exc:
            print(f"alphavantage fehlgeschlagen fuer BTC-EUR: {exc!r}", file=sys.stderr)
            return PriceQuote(ticker, None, "missing", "alphavantage")

    # --- Historische Wochenkurse (fuer den einmaligen Backfill, siehe
    # scripts/backfill_history.py) - liefern die KOMPLETTE verfuegbare
    # Historie in einem einzigen Request, im Gegensatz zu GLOBAL_QUOTE (nur
    # aktueller Kurs). Werfen bewusst statt "missing" zurueckzugeben, da ein
    # Backfill-Lauf bei einem fehlgeschlagenen Ticker abbrechen und nicht
    # eine Luecke fuer die gesamte Historie dieses Tickers stillschweigend
    # hinnehmen soll.

    def fetch_weekly_history(self, ticker: str, since: date) -> dict[date, float]:
        """Woechentliche Schlusskurse eines nicht-Krypto-Tickers ab ``since``,
        in der nativen Waehrung seines Alpha-Vantage-Symbols (EUR fuer
        .DEX/.AMS-Symbole, sonst USD - Umrechnung erfolgt separat, siehe
        ``fetch_fx_weekly_eur_per_usd``)."""
        symbol = ALPHAVANTAGE_SYMBOLS.get(ticker)
        if symbol is None:
            raise ValueError(f"kein Alpha-Vantage-Symbol-Mapping fuer {ticker!r}")
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "TIME_SERIES_WEEKLY", "symbol": symbol, "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        series = data.get("Weekly Time Series")
        if not series:
            raise RuntimeError(f"keine woechentliche Historie fuer {symbol}: {data!r}")
        return _parse_weekly_close_series(series, since)

    def fetch_fx_weekly_eur_per_usd(self, since: date) -> dict[date, float]:
        """Woechentlicher EUR-Gegenwert von 1 USD ab ``since`` (fuer die
        Umrechnung der USD-notierten Einzelaktien beim Backfill)."""
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "FX_WEEKLY", "from_symbol": "USD", "to_symbol": "EUR", "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        series = data.get("Weekly Time Series (FX)")
        if not series:
            raise RuntimeError(f"keine woechentliche FX-Historie fuer USD/EUR: {data!r}")
        return _parse_weekly_close_series(series, since)

    def fetch_crypto_weekly_history(self, since: date) -> dict[date, float]:
        """Woechentliche BTC-EUR-Historie ab ``since``."""
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "DIGITAL_CURRENCY_WEEKLY", "symbol": "BTC", "market": "EUR", "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        series = data.get("Time Series (Digital Currency Weekly)") or data.get(
            "Weekly Time Series (Digital Currency Weekly)"
        )
        if not series:
            raise RuntimeError(f"keine woechentliche Kryptohistorie fuer BTC-EUR: {data!r}")
        result: dict[date, float] = {}
        for date_str, values in series.items():
            d = date.fromisoformat(date_str)
            if d < since:
                continue
            close_key = next((k for k in values if k.startswith("4") and "EUR" in k), None) or next(
                (k for k in values if k.startswith("4.")), None
            )
            if close_key is None:
                continue
            result[d] = float(values[close_key])
        return result


def _parse_weekly_close_series(series: dict, since: date) -> dict[date, float]:
    result: dict[date, float] = {}
    for date_str, values in series.items():
        d = date.fromisoformat(date_str)
        if d < since:
            continue
        close_str = values.get("4. close")
        if close_str is None:
            continue
        result[d] = float(close_str)
    return result
