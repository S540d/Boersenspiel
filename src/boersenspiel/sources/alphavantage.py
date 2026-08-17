"""Alpha-Vantage-Kursquelle: offizielle, API-Key-basierte REST-API statt
Scraping - deutlich zuverlässiger für GitHub Actions als yfinance, das
wiederholt an Yahoos Crumb/Cookie-Authentifizierung scheiterte (siehe README).

Free-Tier-Limits: 25 Requests/Tag, max. 1 Request/Sekunde. Bei aktuell 17
Tickern (7 Barbell-Basisinstrumente + 10 Einzelaktien-Satellit) einmal
wöchentlich noch unproblematisch, lässt aber kaum noch Spielraum für
zusätzliche manuelle Abrufe am selben Tag; zwischen den Requests wird ein
kleiner Sleep eingehalten, um das Sekundenlimit nicht zu reißen.

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
# laufen direkt unter ihrem Ticker in USD, nur SMA Solar (S92) ist analog zu
# den ETFs oben ueber die Xetra-Notierung (".DEX") angebunden.
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
        for i, ticker in enumerate(tickers):
            if i > 0:
                time.sleep(_REQUEST_INTERVAL_SECONDS)
            if ticker == "BTC-EUR":
                results[ticker] = self._fetch_crypto(ticker)
            else:
                results[ticker] = self._fetch_quote(ticker)
        return results

    def _fetch_quote(self, ticker: str) -> PriceQuote:
        symbol = ALPHAVANTAGE_SYMBOLS.get(ticker)
        if symbol is None:
            print(f"alphavantage: kein Symbol-Mapping fuer {ticker}", file=sys.stderr)
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
            return PriceQuote(ticker, float(price_str), "ok", "alphavantage")
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
