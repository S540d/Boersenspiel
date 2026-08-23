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
    assert result["BTC-EUR"][date(2026, 8, 14)] == pytest.approx(52200.0)  # 58000 USD * 0.90


def test_collect_weekly_series_skips_fx_fetch_when_no_usd_tickers():
    source = _FakeSource(weekly={"EUNL": {date(2026, 8, 14): 85.0}}, fx={}, crypto={})

    def _fail(*args, **kwargs):
        raise AssertionError("FX_WEEKLY sollte ohne USD-Ticker nicht abgerufen werden")

    source.fetch_fx_weekly_eur_per_usd = _fail
    result = bh.collect_weekly_series(source, ["EUNL"], since=date(2020, 1, 1))

    assert result["EUNL"][date(2026, 8, 14)] == 85.0


def test_collect_weekly_series_fetches_fx_for_crypto_even_without_other_usd_tickers():
    """BTC-EUR laeuft ueber DIGITAL_CURRENCY_WEEKLY(market=USD) + FX_WEEKLY
    (Issue #56) - der FX-Abruf darf deshalb nicht (mehr) daran haengen, ob
    daneben noch eine "echte" USD-Aktie im Ticker-Set ist."""
    source = _FakeSource(
        weekly={"EUNL": {date(2026, 8, 14): 85.0}},
        fx={date(2026, 8, 14): 0.90},
        crypto={date(2026, 8, 14): 58000.0},
    )
    result = bh.collect_weekly_series(source, ["EUNL", "BTC-EUR"], since=date(2020, 1, 1))

    assert result["BTC-EUR"][date(2026, 8, 14)] == pytest.approx(52200.0)  # 58000 USD * 0.90


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


# --- Keine Rueckwaerts-Extrapolation des Wechselkurses (#62) ------------------


def test_nearest_fx_rate_does_not_extrapolate_backwards():
    """Vor Beginn der FX-Reihe gibt es keinen Kurs - und keinen Ersatz dafuer.

    Vorher fiel die Funktion auf den aeltesten VERFUEGBAREN Kurs zurueck, der
    aber juenger ist als das umzurechnende Datum. Im echten 20-Jahres-Backfill
    beginnt Alpha Vantages FX_WEEKLY erst im November 2014; dadurch wurden 227
    Wochen aller USD-Ticker und die komplette fruehe BTC-Historie mit dem
    konstanten Kurs von 2014 umgerechnet.
    """
    rates = {date(2014, 11, 21): 0.7982, date(2014, 11, 28): 0.8028}
    sorted_dates = sorted(rates)

    # Datum VOR der Reihe -> kein Kurs.
    assert bh._nearest_fx_rate(rates, sorted_dates, date(2010, 7, 9)) is None
    # Datum in/nach der Reihe -> Forward-Fill wie bisher.
    assert bh._nearest_fx_rate(rates, sorted_dates, date(2014, 11, 21)) == pytest.approx(0.7982)
    assert bh._nearest_fx_rate(rates, sorted_dates, date(2026, 1, 1)) == pytest.approx(0.8028)


def test_collect_weekly_series_drops_usd_weeks_before_the_fx_history_starts():
    source = _FakeSource(
        weekly={"LITE": {date(2010, 7, 9): 100.0, date(2026, 8, 14): 200.0}},
        fx={date(2014, 11, 21): 0.80, date(2026, 8, 14): 0.90},
        crypto={date(2010, 7, 9): 0.05, date(2026, 8, 14): 58000.0},
    )
    result = bh.collect_weekly_series(source, ["LITE", "BTC-EUR"], since=date(2006, 1, 1))

    # Woche vor Beginn der FX-Reihe faellt weg, statt mit dem 2014er-Kurs
    # falsch umgerechnet zu werden - record_week() traegt dafuer "missing" ein.
    assert date(2010, 7, 9) not in result["LITE"]
    assert date(2010, 7, 9) not in result["BTC-EUR"]
    assert result["LITE"][date(2026, 8, 14)] == pytest.approx(180.0)
    assert result["BTC-EUR"][date(2026, 8, 14)] == pytest.approx(52200.0)


# --- Handgepflegte Ergaenzungsdateien (#62) -----------------------------------
#
# Der Backfill setzt price_history.csv komplett zurueck. Manuell recherchierte
# Kurse (frueheste BTC-Historie) und Wechselkurse (EUR/USD vor November 2014,
# das FX_WEEKLY nicht liefert) leben deshalb in eigenen Dateien, die kein
# Skript schreibt und die bei jedem Lauf neu eingemischt werden.


def _schreibe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_read_manual_fx_liest_werte_und_ueberspringt_kommentare(tmp_path: Path):
    _schreibe(
        tmp_path / bh.MANUAL_FX_FILE,
        "# eine Kommentarzeile\nDate,EUR_pro_USD\n2010-07-09,0.7912\n2011-05-13,0.7010\n",
    )
    assert bh.read_manual_fx(tmp_path) == {
        date(2010, 7, 9): pytest.approx(0.7912),
        date(2011, 5, 13): pytest.approx(0.7010),
    }


def test_read_manual_prices_liest_langformat(tmp_path: Path):
    _schreibe(
        tmp_path / bh.MANUAL_PRICES_FILE,
        "# Kommentar\nDate,Ticker,Preis_EUR\n2010-07-23,BTC-EUR,0.0391\n2010-07-30,BTC-EUR,0.0450\n",
    )
    assert bh.read_manual_prices(tmp_path) == {
        "BTC-EUR": {
            date(2010, 7, 23): pytest.approx(0.0391),
            date(2010, 7, 30): pytest.approx(0.0450),
        }
    }


def test_fehlende_ergaenzungsdateien_sind_kein_fehler(tmp_path: Path):
    assert bh.read_manual_fx(tmp_path) == {}
    assert bh.read_manual_prices(tmp_path) == {}


def test_read_manual_prices_bricht_bei_unbekanntem_ticker_ab(tmp_path: Path):
    # Ein Tippfehler im Ticker wuerde sonst still ignoriert - und der
    # muehsam recherchierte Kurs landete nie in der Historie.
    _schreibe(tmp_path / bh.MANUAL_PRICES_FILE, "Date,Ticker,Preis_EUR\n2010-07-23,BTCEUR,0.0391\n")
    with pytest.raises(ValueError, match="BTCEUR"):
        bh.read_manual_prices(tmp_path)


def test_manual_fx_macht_wochen_vor_der_fx_reihe_umrechenbar():
    """Der eigentliche Zweck der FX-Datei: eine einzige gepflegte Zeile macht
    die Woche fuer alle USD-Instrumente auf einmal umrechenbar."""
    source = _FakeSource(
        weekly={"LITE": {date(2010, 7, 9): 100.0, date(2026, 8, 14): 200.0}},
        fx={date(2014, 11, 21): 0.80, date(2026, 8, 14): 0.90},
        crypto={date(2010, 7, 9): 0.05, date(2026, 8, 14): 58000.0},
    )
    ohne = bh.collect_weekly_series(source, ["LITE", "BTC-EUR"], since=date(2006, 1, 1))
    assert date(2010, 7, 9) not in ohne["LITE"]
    assert date(2010, 7, 9) not in ohne["BTC-EUR"]

    mit = bh.collect_weekly_series(
        source,
        ["LITE", "BTC-EUR"],
        since=date(2006, 1, 1),
        manual_fx={date(2010, 7, 9): 0.7912},
    )
    assert mit["LITE"][date(2010, 7, 9)] == pytest.approx(79.12)
    assert mit["BTC-EUR"][date(2010, 7, 9)] == pytest.approx(0.05 * 0.7912)
    # Die API-Wochen bleiben unveraendert.
    assert mit["LITE"][date(2026, 8, 14)] == pytest.approx(180.0)


def test_manual_prices_werden_nicht_noch_einmal_umgerechnet():
    """Handgepflegte Kurse sind bereits in EUR - sie duerfen nicht durch die
    USD/EUR-Umrechnung laufen, sonst waeren sie um den Wechselkurs daneben."""
    source = _FakeSource(
        weekly={"LITE": {date(2026, 8, 14): 200.0}},
        fx={date(2026, 8, 14): 0.90},
        crypto={},
    )
    result = bh.collect_weekly_series(
        source,
        ["LITE", "BTC-EUR"],
        since=date(2006, 1, 1),
        manual_prices={"BTC-EUR": {date(2010, 7, 23): 0.0391}, "LITE": {date(2010, 7, 9): 50.0}},
    )
    assert result["BTC-EUR"][date(2010, 7, 23)] == pytest.approx(0.0391)
    assert result["LITE"][date(2010, 7, 9)] == pytest.approx(50.0)  # nicht * 0.90


def test_manual_prices_gewinnen_gegen_die_api_in_derselben_iso_woche():
    """Abgeglichen wird auf ISO-Wochen-Ebene: ein manueller Eintrag vom
    Freitag ersetzt den API-Wert vom Donnerstag derselben Woche, statt als
    zweiter Wert derselben Woche danebenzustehen."""
    source = _FakeSource(
        weekly={"LITE": {date(2026, 8, 13): 200.0}},  # Donnerstag
        fx={date(2026, 8, 13): 1.0},
        crypto={},
    )
    result = bh.collect_weekly_series(
        source,
        ["LITE"],
        since=date(2006, 1, 1),
        manual_prices={"LITE": {date(2026, 8, 14): 111.0}},  # Freitag, gleiche ISO-Woche
    )
    assert result["LITE"] == {date(2026, 8, 14): pytest.approx(111.0)}


def test_manual_prices_ueberleben_den_schreibvorgang(tmp_path: Path):
    """End-to-End-Absicherung des eigentlichen Anliegens: nach dem kompletten
    Neuaufbau von price_history.csv steht der handgepflegte Kurs noch drin."""
    per_ticker = bh.collect_weekly_series(
        _FakeSource(weekly={"EUNL": {date(2026, 8, 14): 85.0}}, fx={}, crypto={}),
        ["EUNL", "BTC-EUR"],
        since=date(2006, 1, 1),
        manual_prices={"BTC-EUR": {date(2010, 7, 23): 0.0391}},
    )
    bh.write_backfilled_history(per_ticker, tmp_path)

    rows = {r.date: r.prices for r in read_price_history(data_dir=tmp_path)}
    assert rows[date(2010, 7, 23)]["BTC-EUR"] == Decimal("0.0391")


def test_manual_prices_fuer_nicht_angefragte_ticker_werden_ignoriert():
    """Schutz gegen versehentliches Einschleusen: collect_weekly_series
    liefert nur Ticker, die auch angefragt wurden."""
    source = _FakeSource(weekly={"EUNL": {date(2026, 8, 14): 85.0}}, fx={}, crypto={})
    result = bh.collect_weekly_series(
        source,
        ["EUNL"],
        since=date(2006, 1, 1),
        manual_prices={"TSLA": {date(2010, 7, 9): 1.16}},
    )
    assert "TSLA" not in result


def test_fx_luecken_findet_loecher_mittendrin():
    """Sobald manual_fx_usd_eur.csv die Fruehphase abdeckt, beginnt die
    FX-Reihe 2006 - eine reine Beginn-Pruefung wuerde ein Loch zwischen dem
    Ende der handgepflegten Daten und dem Beginn der API-Abdeckung dann nicht
    mehr sehen. Genau das passiert, wenn Alpha Vantages FX_WEEKLY-Fenster mit
    der Zeit nach vorne wandert."""
    dates = [date(2006, 1, 2), date(2006, 1, 9), date(2014, 11, 21), date(2014, 11, 28)]
    assert bh._fx_luecken(dates, since=date(2006, 1, 1)) == [(date(2006, 1, 9), date(2014, 11, 21))]


def test_fx_luecken_meldet_lueckenlose_reihe_nicht():
    dates = [date(2026, 8, 7), date(2026, 8, 14), date(2026, 8, 21)]
    assert bh._fx_luecken(dates, since=date(2026, 8, 1)) == []


def test_fx_luecken_meldet_weiterhin_einen_zu_spaeten_beginn():
    dates = [date(2014, 11, 21), date(2014, 11, 28)]
    assert bh._fx_luecken(dates, since=date(2006, 1, 1)) == [(date(2006, 1, 1), date(2014, 11, 21))]


def test_fx_luecken_toleriert_einzelne_ausgefallene_wochen():
    # Feiertagswoche ohne Kurs ist kein Abdeckungsloch.
    dates = [date(2026, 8, 7), date(2026, 8, 21)]
    assert bh._fx_luecken(dates, since=date(2026, 8, 1)) == []


def test_mitgelieferte_fx_datei_deckt_die_luecke_bis_zum_api_beginn():
    """Regressionsschutz fuer die eingecheckte data/manual_fx_usd_eur.csv:
    sie muss den Zeitraum vor dem Beginn von FX_WEEKLY (2014-11-21) ohne
    eigene Loecher abdecken."""
    fx = bh.read_manual_fx(Path(__file__).resolve().parents[1] / "data")
    assert fx, "manual_fx_usd_eur.csv sollte die EZB-Referenzkurse enthalten"
    ds = sorted(fx)
    assert ds[0] <= date(2006, 9, 1)  # aelteste Zeile in price_history.csv
    assert ds[-1] >= date(2014, 11, 14)
    assert bh._fx_luecken(ds, since=ds[0]) == []
    # Kehrwert-Konvention: 1 USD kostet deutlich weniger als 1 EUR im Juli 2008
    # (EUR/USD-Hoch 1,599) und etwa 0,79 EUR im Juli 2010.
    assert fx[date(2008, 7, 15)] == pytest.approx(0.625391, abs=1e-6)
    assert fx[date(2010, 7, 9)] == pytest.approx(0.791327, abs=1e-6)


# --- Request-Budget (#64) -----------------------------------------------------
#
# Alpha Vantages Free Tier erlaubt 25 Requests pro Tag und API-Key. Mit den
# sieben Datenreihen aus #64 sind beide Laeufe bei exakt 25 - es gibt keinen
# Puffer mehr. Ein weiteres Instrument macht jeden Lauf unmoeglich, und ein
# nicht aufloesbares Symbol bricht den Backfill ab, ohne dass die bereits
# verbrauchten Requests zurueckkommen. Deshalb als Test statt als Kommentar.

_ALPHAVANTAGE_TAGESLIMIT = 25


def _backfill_requests() -> int:
    """1x FX_WEEKLY + 1x DIGITAL_CURRENCY_WEEKLY + je 1x TIME_SERIES pro
    nicht-Krypto-Ticker."""
    return 1 + 1 + len([t for t in TICKERS if t != "BTC-EUR"])


def _wochenabruf_requests() -> int:
    """1x CURRENCY_EXCHANGE_RATE (einmal je fetch(), nicht je Ticker) + je 1x
    GLOBAL_QUOTE bzw. Krypto-Endpunkt pro Ticker."""
    return 1 + len(TICKERS)


def test_backfill_passt_in_das_tageslimit():
    assert _backfill_requests() <= _ALPHAVANTAGE_TAGESLIMIT, (
        f"Backfill braucht {_backfill_requests()} Requests, erlaubt sind "
        f"{_ALPHAVANTAGE_TAGESLIMIT}. Ein Instrument entfernen oder Premium-Key."
    )


def test_wochenabruf_passt_in_das_tageslimit():
    assert _wochenabruf_requests() <= _ALPHAVANTAGE_TAGESLIMIT, (
        f"Wochenabruf braucht {_wochenabruf_requests()} Requests, erlaubt sind "
        f"{_ALPHAVANTAGE_TAGESLIMIT}."
    )


def test_neue_datenreihen_sind_in_euro_notiert():
    """Die sieben Instrumente aus #64 sind bewusst XETRA-Symbole. Landete eines
    in USD_TICKERS, braeuchte es die Umrechnung - und haette damit vor November
    2014 gar keinen Kurs, weil FX_WEEKLY erst dann beginnt (#62)."""
    from boersenspiel.sources.alphavantage import ALPHAVANTAGE_SYMBOLS, USD_TICKERS

    neue = ["IUSA", "XEON", "EXSA", "IBCL", "IBCI", "IQQ6", "EXXY"]
    for ticker in neue:
        assert ticker in TICKERS, f"{ticker} fehlt in instruments.py"
        assert ALPHAVANTAGE_SYMBOLS[ticker].endswith(".DEX"), ticker
        assert ticker not in USD_TICKERS, f"{ticker} braeuchte sonst FX-Umrechnung"


def test_neue_datenreihen_bleiben_ausserhalb_der_urspruenglichen_strategien():
    """Die drei ursprünglichen Barbell-Strategien und alle Szenarien (die
    strukturell BARBELL_20_80.toepfe wiederverwenden) duerfen von den sieben in
    #64 ergaenzten Instrumenten weiterhin unberuehrt bleiben - nur die beiden
    dafuer neu geschaffenen Strategien (BARBELL_20_80_DIVERSIFIZIERT,
    SP500_BENCHMARK) allokieren sie (#64, zweiter Umsetzungsschritt)."""
    from boersenspiel.scenarios import SCENARIOS
    from boersenspiel.strategies import (
        BARBELL_20_60_20_SATELLIT,
        BARBELL_20_80,
        BARBELL_30_70,
    )

    neue = {"IUSA", "XEON", "EXSA", "IBCL", "IBCI", "IQQ6", "EXXY"}
    unveraendert = [BARBELL_20_80, BARBELL_30_70, BARBELL_20_60_20_SATELLIT, *SCENARIOS]
    for strategie in unveraendert:
        allokiert = set(strategie.alle_ticker_gewichte())
        assert not (allokiert & neue), f"{strategie.name} allokiert {allokiert & neue}"
