"""Alpha-Vantage-Kursquelle: offizielle, API-Key-basierte REST-API statt
Scraping - deutlich zuverlässiger für GitHub Actions als yfinance, das
wiederholt an Yahoos Crumb/Cookie-Authentifizierung scheiterte (siehe README).

Free-Tier-Limits: 25 Requests/Tag, max. 1 Request/Sekunde. Die aktuell 26
Ticker brauchen 27 Requests und passen damit nicht mehr in einen Tag - der
wöchentliche Abruf läuft deshalb seit #99 in zwei Batches an zwei
aufeinanderfolgenden Tagen (siehe ``batch_tickers`` weiter unten). Zwischen
den Requests wird ein kleiner Sleep eingehalten, um das Sekundenlimit nicht zu
reißen.

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
    # Datenreihen ohne Allokation (#64) - alle sieben bewusst als XETRA-Symbol
    # in EUR. Damit faellt kein zusaetzlicher FX-Request an, und das gesamte
    # Waehrungsproblem aus #62 entsteht fuer sie gar nicht erst (FX_WEEKLY
    # beginnt erst im November 2014). Am 22.08.2026 per SYMBOL_SEARCH geprueft:
    # jedes Symbol loest auf XETRA in EUR auf. Das ist wichtig, weil ein nicht
    # aufloesbares Symbol den kompletten Backfill abbricht - bei 25 von 25
    # Requests waere damit das Tagesbudget verbraucht.
    "IUSA": "IUSA.DEX",
    "XEON": "XEON.DEX",
    "EXSA": "EXSA.DEX",
    "IBCL": "IBCL.DEX",
    "IBCI": "IBCI.DEX",
    "IQQ6": "IQQ6.DEX",
    "EXXY": "EXXY.DEX",
    # Dividende und Value (#99) - ebenfalls XETRA/EUR. Am 28.08.2026 per
    # SYMBOL_SEARCH und GLOBAL_QUOTE geprueft. Der im Issue vorgeschlagene
    # Value-Ticker "IUVL" loest bei Alpha Vantage NICHT auf; die XETRA-Notierung
    # desselben Fonds laeuft unter "IS3S.DEX" (die Alternative "IWVL.LON" waere
    # USD-notiert und damit FX-pflichtig, siehe #62).
    "ISPA": "ISPA.DEX",
    "IS3S": "IS3S.DEX",
}

# Ticker, deren Alpha-Vantage-Symbol in USD notiert - Kurs wird bei jedem
# Abruf per aktuellem EUR/USD-Kurs in EUR umgerechnet.
USD_TICKERS: frozenset[str] = frozenset({"LITE", "BYDDY", "SEDG", "TSLA", "PLTR", "MSTR", "RIVN", "KO", "RHHBY"})

# --- Wochenabruf in zwei Batches (#99) ---------------------------------------
#
# Alpha Vantages Free Tier erlaubt 25 Requests pro Tag und API-Key. Seit den
# beiden Instrumenten aus #99 braucht ein Abruf ALLER Ticker 27 Requests
# (26 Ticker + 1x CURRENCY_EXCHANGE_RATE) und passt damit nicht mehr in einen
# einzigen Tag. Der Wochenabruf laeuft deshalb an zwei aufeinanderfolgenden
# Tagen, jeder Lauf holt nur seinen Batch (siehe scripts/run_fetch.py --batch
# und .github/workflows/weekly-update.yml). Jeder Ticker wird weiterhin genau
# einmal pro Woche aktualisiert, nur eben nicht mehr alle am selben Wochentag;
# history_store.record_week() mischt den zweiten Teilabruf additiv in die
# bereits geschriebene Zeile derselben ISO-Woche.
#
# Die Aufteilung wird BEWUSST abgeleitet statt als zwei feste Listen
# hinterlegt: Batch 1 sind genau die Ticker, die einen Fremdwaehrungs- oder
# Krypto-Endpunkt brauchen (alle USD_TICKERS plus BTC-EUR), Batch 2 der Rest.
# Damit faellt der EUR/USD-Request zwangslaeufig nur in einem der beiden Laeufe
# an - er wird in fetch() einmal je Aufruf geholt und gilt fuer alle
# USD-Ticker gemeinsam. Ein kuenftig ergaenztes Instrument landet ausserdem
# automatisch im richtigen Batch, statt eine hinterlegte Liste stillschweigend
# unvollstaendig zu lassen.
#
# Request-Bilanz aktuell: Batch 1 = 9 USD-Ticker + FX + BTC-EUR = 11 Requests,
# Batch 2 = 16 EUR/XETRA-Ticker = 16 Requests. Beide klar unter 25; der
# Spielraum liegt in Batch 2 (siehe tests/test_backfill_history.py).
FETCH_BATCHES = (1, 2)


def batch_tickers(tickers: list[str], batch: int) -> list[str]:
    """Die Teilmenge von ``tickers``, die im angegebenen Wochen-Batch abgerufen
    wird. ``batch`` ausserhalb von ``FETCH_BATCHES`` ist ein Programmierfehler."""
    if batch not in FETCH_BATCHES:
        raise ValueError(f"Unbekannter Batch {batch!r}, erlaubt sind {FETCH_BATCHES}")
    fremdwaehrung = [t for t in tickers if t in USD_TICKERS or t == "BTC-EUR"]
    if batch == 1:
        return fremdwaehrung
    return [t for t in tickers if t not in fremdwaehrung]

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
                note = _rate_limit_note(data)
                if note:
                    print(f"alphavantage: Rate-Limit erkannt fuer EUR/USD-Kurs: {note}", file=sys.stderr)
                else:
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
            quote = data.get("Global Quote", {})
            price_str = quote.get("05. price")
            if not price_str:
                note = _rate_limit_note(data)
                if note:
                    print(f"alphavantage: Rate-Limit erkannt fuer {symbol}: {note}", file=sys.stderr)
                    return PriceQuote(ticker, None, "rate_limited", "alphavantage")
                print(f"alphavantage: keine Kursdaten fuer {symbol}: {data!r}", file=sys.stderr)
                return PriceQuote(ticker, None, "missing", "alphavantage")
            price = float(price_str)
            if fx_rate is not None:
                price *= fx_rate
            return PriceQuote(ticker, price, "ok", "alphavantage", _parse_trading_day(quote.get("07. latest trading day")))
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
                note = _rate_limit_note(data)
                if note:
                    print(f"alphavantage: Rate-Limit erkannt fuer BTC-EUR: {note}", file=sys.stderr)
                    return PriceQuote(ticker, None, "rate_limited", "alphavantage")
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
            return PriceQuote(
                ticker, float(latest[close_key]), "ok", "alphavantage", _parse_trading_day(latest_date)
            )
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
        """Woechentliche, SPLITBEREINIGTE Schlusskurse eines nicht-Krypto-Tickers
        ab ``since``, in der nativen Waehrung seines Alpha-Vantage-Symbols (EUR
        fuer .DEX/.AMS-Symbole, sonst USD - Umrechnung erfolgt separat, siehe
        ``fetch_fx_weekly_eur_per_usd``).

        Nutzt ``TIME_SERIES_WEEKLY_ADJUSTED`` statt ``TIME_SERIES_WEEKLY`` (#62).
        Der unbereinigte Endpunkt liefert den nominalen Schlusskurs: bei jedem
        Aktiensplit faellt die Kursreihe um den Split-Faktor, ohne dass ein
        Anleger etwas verloren haette. In der 20-Jahres-Historie betraf das
        fuenf der zehn Satelliten-Aktien und erzeugte Phantom-Wochenverluste bis
        -91% (TSLA 5:1 2020 und 3:1 2022, MSTR 10:1 2024, KO 2:1 2012, RHHBY und
        BYDDY je ein ADR-Verhaeltniswechsel).

        Bewusst SPLIT-only statt des vollen ``adjusted close``: der adjustierte
        Kurs ist eine Total-Return-Reihe, rechnet also auch Dividenden ein. Die
        Simulation modelliert Bar-Ausschuettungen aber bereits explizit als
        eigenen, steuerlich korrekt behandelten Kapitalertrag
        (``strategies.DIVIDENDENRENDITE_PLATZHALTER``, ``Instrument.ausschuettend``,
        #57). Der volle adjusted close wuerde sie ein zweites Mal als
        Kurssteigerung enthalten.
        """
        symbol = ALPHAVANTAGE_SYMBOLS.get(ticker)
        if symbol is None:
            raise ValueError(f"kein Alpha-Vantage-Symbol-Mapping fuer {ticker!r}")
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "TIME_SERIES_WEEKLY_ADJUSTED", "symbol": symbol, "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        return _split_bereinigte_close_series(_extract_time_series(resp.json(), symbol), since)

    def fetch_fx_weekly_eur_per_usd(self, since: date) -> dict[date, float]:
        """Woechentlicher EUR-Gegenwert von 1 USD ab ``since`` (fuer die
        Umrechnung der USD-notierten Einzelaktien beim Backfill)."""
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "FX_WEEKLY", "from_symbol": "USD", "to_symbol": "EUR", "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        return _parse_weekly_close_series(_extract_time_series(resp.json(), "USD/EUR (FX_WEEKLY)"), since)

    def fetch_crypto_weekly_history(self, since: date) -> dict[date, float]:
        """Woechentliche BTC-**USD**-Historie ab ``since`` (native Waehrung des
        Symbols, analog zu ``fetch_weekly_history`` - Umrechnung nach EUR
        erfolgt separat ueber ``fetch_fx_weekly_eur_per_usd``, siehe
        ``scripts/backfill_history.py``).

        Bewusst ``market=USD`` statt ``market=EUR``: Alpha Vantage liefert
        ``DIGITAL_CURRENCY_WEEKLY`` fuer ``market=EUR`` im Free-Tier nur ca. 50
        Wochen Historie zurueck (statt der vollen Historie seit 2010), fuer
        ``market=USD`` dagegen die komplette verfuegbare Historie (Issue #56,
        manuell mit dem Alpha-Vantage-MCP-Server verifiziert am 21.08.2026:
        50 vs. 840 Wochen bei sonst identischer Anfrage) - derselbe Grund,
        aus dem die Einzelaktien in ``USD_TICKERS`` schon ueber USD + FX_WEEKLY
        laufen statt ueber eine (nicht fuer jeden Wert existierende)
        EUR-Notierung.
        """
        resp = requests.get(
            ALPHAVANTAGE_URL,
            params={"function": "DIGITAL_CURRENCY_WEEKLY", "symbol": "BTC", "market": "USD", "apikey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        series = _extract_time_series(resp.json(), "BTC-USD (DIGITAL_CURRENCY_WEEKLY)")
        result: dict[date, float] = {}
        for date_str, values in series.items():
            d = date.fromisoformat(date_str)
            if d < since:
                continue
            close_key = next((k for k in values if k.startswith("4") and "USD" in k), None) or next(
                (k for k in values if k.startswith("4.")), None
            )
            if close_key is None:
                continue
            result[d] = float(values[close_key])
        return result


def _extract_time_series(data: dict, kontext: str) -> dict:
    """Zieht die Zeitreihe aus einer Alpha-Vantage-Antwort, ohne den
    Schluesselnamen fest zu verdrahten.

    Alpha Vantage benennt den Zeitreihen-Schluessel je Endpunkt anders und
    ohne erkennbares Muster::

        TIME_SERIES_WEEKLY       -> "Weekly Time Series"
        FX_WEEKLY                -> "Time Series FX (Weekly)"
        DIGITAL_CURRENCY_WEEKLY  -> "Time Series (Digital Currency Weekly)"

    Ein fest eingetragener Name bricht deshalb still, sobald ein Endpunkt
    dazukommt oder anders heisst als vermutet - genau daran ist der erste
    Backfill-Lauf gescheitert (FX_WEEKLY war als "Weekly Time Series (FX)"
    eingetragen). Statt zu raten wird der einzige Eintrag genommen, der ein
    Objekt ist; "Meta Data" ist ausgenommen.

    Fehler- und Rate-Limit-Antworten ("Note"/"Information"/"Error Message")
    haben ausschliesslich String-Werte und liefern damit automatisch die
    aussagekraeftige Fehlermeldung statt einer stillen Luecke.
    """
    kandidaten = {k: v for k, v in data.items() if k != "Meta Data" and isinstance(v, dict)}
    if len(kandidaten) == 1:
        return next(iter(kandidaten.values()))
    if not kandidaten:
        raise RuntimeError(f"keine Zeitreihe in der Antwort fuer {kontext}: {_kurz(data)}")
    raise RuntimeError(
        f"mehrdeutige Antwort fuer {kontext}, Zeitreihen-Kandidaten {sorted(kandidaten)}: {_kurz(data)}"
    )


def _kurz(data: object, grenze: int = 500) -> str:
    """Gekuerzte Darstellung fuer Fehlermeldungen - eine vollstaendige
    Kurshistorie im Traceback macht das Log unlesbar."""
    text = repr(data)
    return text if len(text) <= grenze else text[:grenze] + " ... (gekuerzt)"


def _rate_limit_note(data: dict) -> str | None:
    """Erkennt Alpha Vantages Rate-Limit-/Fehlerantworten, die bei erreichtem
    Tageslimit statt der erwarteten Kursdaten mit HTTP 200 zurueckkommen (kein
    "Global Quote" o.ae., stattdessen "Note"/"Information"/"Error Message").
    Ohne diese Erkennung ist ein erschoepftes Tageslimit von einer echten
    Kurslücke nicht unterscheidbar - beide liefern denselben Status "missing"."""
    for key in ("Note", "Information", "Error Message"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_trading_day(raw: str | None) -> date | None:
    """Handelstag aus einer Alpha-Vantage-Antwort, oder None wenn das Feld
    fehlt/unlesbar ist - ein unbrauchbares Datum darf den Kursabruf nicht
    scheitern lassen, der Aufrufer faellt dann auf das Abrufdatum zurueck."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        print(f"alphavantage: unlesbarer Handelstag {raw!r}", file=sys.stderr)
        return None


# Ab diesem Verhaeltnis-Sprung zwischen zwei aufeinanderfolgenden Wochen gilt
# eine Aenderung des Bereinigungsfaktors als Split (bzw. ADR-Verhaeltniswechsel)
# und nicht als Dividende. Der kleinste uebliche Split ist 5:4 (=1,25); die
# groesste denkbare Wochendividende liegt deutlich darunter.
_SPLIT_SCHWELLE = 1.15


def _split_bereinigte_close_series(series: dict, since: date) -> dict[date, float]:
    """Splitbereinigte (aber NICHT dividendenbereinigte) Wochenschlusskurse aus
    einer ``TIME_SERIES_WEEKLY_ADJUSTED``-Antwort.

    Alpha Vantage liefert je Woche ``4. close`` (nominal) und
    ``5. adjusted close`` (Total Return: split- UND dividendenbereinigt), aber
    keinen Split-Koeffizienten. Der kumulierte Bereinigungsfaktor
    ``close / adjusted close`` faellt bei Dividenden nur langsam, bei einem
    Split dagegen in einem einzigen Schritt um den Split-Faktor. Diese Funktion
    isoliert deshalb genau diese Spruenge und teilt die nominalen Kurse durch
    den ab der jeweiligen Woche kumulierten Split-Faktor. Dividenden bleiben
    damit im Kursverlauf unberuecksichtigt - so wie es die Simulation erwartet,
    die sie separat als Barertrag modelliert (siehe ``fetch_weekly_history``).
    """
    beobachtungen: list[tuple[date, float, float]] = []
    for date_str, values in series.items():
        close_str = values.get("4. close")
        adj_str = values.get("5. adjusted close")
        if close_str is None:
            continue
        close = float(close_str)
        adj = float(adj_str) if adj_str is not None else 0.0
        beobachtungen.append((date.fromisoformat(date_str), close, adj))
    beobachtungen.sort()

    # Kumulierten Split-Faktor je Woche bestimmen: rueckwaerts vom Ende her,
    # damit die juengste Woche (per Definition unbereinigt) den Faktor 1 hat.
    faktoren: dict[date, float] = {}
    kumuliert = 1.0
    vorheriges_verhaeltnis: float | None = None
    for d, close, adj in reversed(beobachtungen):
        verhaeltnis = close / adj if adj > 0 else None
        if verhaeltnis is not None and vorheriges_verhaeltnis is not None:
            sprung = verhaeltnis / vorheriges_verhaeltnis
            if sprung >= _SPLIT_SCHWELLE:
                kumuliert *= sprung
        if verhaeltnis is not None:
            vorheriges_verhaeltnis = verhaeltnis
        faktoren[d] = kumuliert

    return {
        d: close / faktoren[d]
        for d, close, _adj in beobachtungen
        if d >= since and faktoren.get(d, 0.0) > 0
    }


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
