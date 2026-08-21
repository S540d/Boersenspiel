"""Standard-Kursquelle für GitHub Actions: yfinance primär, Stooq-CSV als Fallback.

GitHub-Actions-Runner haben Internetzugang zu öffentlichen Kursquellen - das
funktioniert problemlos. Das Ticker-zu-Symbol-Mapping für beide Anbieter liegt
ausschließlich hier, nicht in ``instruments.py`` - Instrumente bleiben dadurch
quellenunabhängig definiert, und diese Quelle kann jederzeit gegen eine andere
ausgetauscht werden, ohne den Rest des Systems anzufassen. Aktiver Standardweg
ist inzwischen ``alphavantage.py`` (siehe dort); diese Datei bleibt als
Referenzimplementierung erhalten, wird von ``scripts/run_fetch.py`` aber nicht
mehr aufgerufen.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date

import requests

from . import PriceQuote

# Stooq blockt/limitiert Anfragen ohne Browser-artigen User-Agent haeufiger als
# solche mit einem - reduziert Fehlschlaege durch simple Bot-Erkennung.
_STOOQ_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; boersenspiel-fetch/1.0)"}

# Ticker (aus instruments.py) -> yfinance-Symbol
YFINANCE_SYMBOLS: dict[str, str] = {
    "EUNL": "EUNL.DE",
    "EUNA": "EUNA.DE",
    "4GLD": "4GLD.DE",
    "LYMS": "LYMS.DE",
    "SEMI": "SEMI.DE",
    "EIMI": "EIMI.DE",
    "BTC-EUR": "BTC-EUR",
}

# Ticker -> Stooq-Symbol (abweichende Symbolik, insb. bei BTC-EUR)
STOOQ_SYMBOLS: dict[str, str] = {
    "EUNL": "eunl.de",
    "EUNA": "euna.de",
    "4GLD": "4gld.de",
    "LYMS": "lyms.de",
    "SEMI": "semi.de",
    "EIMI": "eimi.de",
    "BTC-EUR": "btceur",
}

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


class YfinanceStooqSource:
    """PriceSource-Implementierung: yfinance primär, Stooq-CSV als Fallback."""

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, PriceQuote]:
        results: dict[str, PriceQuote] = {}
        for ticker in tickers:
            quote = self._fetch_yfinance(ticker) or self._fetch_stooq(ticker)
            if quote is None:
                quote = PriceQuote(ticker=ticker, price=None, status="missing", source="none")
            results[ticker] = quote
        return results

    def _fetch_yfinance(self, ticker: str) -> PriceQuote | None:
        symbol = YFINANCE_SYMBOLS.get(ticker)
        if symbol is None:
            return None
        try:
            import yfinance as yf

            hist = yf.Ticker(symbol).history(period="5d")
            if hist.empty:
                print(f"yfinance: leere Historie fuer {symbol}", file=sys.stderr)
                return None
            last_close = float(hist["Close"].iloc[-1])
            return PriceQuote(ticker=ticker, price=last_close, status="ok", source="yfinance")
        except Exception as exc:
            print(f"yfinance fehlgeschlagen fuer {symbol}: {exc!r}", file=sys.stderr)
            return None

    def _fetch_stooq(self, ticker: str) -> PriceQuote | None:
        symbol = STOOQ_SYMBOLS.get(ticker)
        if symbol is None:
            return None
        try:
            resp = requests.get(STOOQ_URL.format(symbol=symbol), timeout=15, headers=_STOOQ_HEADERS)
            resp.raise_for_status()
            reader = csv.DictReader(io.StringIO(resp.text))
            rows = [r for r in reader if r.get("Close") not in (None, "", "N/D")]
            if not rows:
                print(f"stooq: keine verwertbaren Daten fuer {symbol}: {resp.text[:200]!r}", file=sys.stderr)
                return None
            close = float(rows[-1]["Close"])
            return PriceQuote(ticker=ticker, price=close, status="ok", source="stooq")
        except Exception as exc:
            print(f"stooq fehlgeschlagen fuer {symbol}: {exc!r}", file=sys.stderr)
            return None
