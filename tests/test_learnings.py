"""Tests für die abgeleiteten Key Learnings (``learnings.py``).

Kernpunkt der Prüfung: die Aussagen sind **nicht** hinterlegt, sondern folgen
den übergebenen Zahlen. Deshalb wird jede Regel gegen konstruierte Views mit
bekannten Werten gefahren und geprüft, dass die genannten Zahlen und Namen
mitwandern - und dass eine Regel still wegfällt, wenn ihre Frage aus den Daten
nicht beantwortbar ist.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from boersenspiel.dashboard import build_dashboard
from boersenspiel.history_store import PriceRow
from boersenspiel.learnings import derive_learnings
from boersenspiel.strategies import Strategy, Topf


def _einfache_strategie(name: str) -> Strategy:
    return Strategy(
        name=name,
        startkapital=Decimal("1000"),
        toepfe=[Topf(name="Topf", gewicht_gesamt=Decimal("1"), sub_gewichte={"T1": Decimal("1")})],
        ziel_topf="Topf",
        ziel_gewicht=Decimal("1"),
        rebalancing_schwelle_pp=Decimal("1000"),
    )


def _rows() -> list[PriceRow]:
    return [
        PriceRow(date(2024, 1, 1), {"T1": Decimal("100")}),
        PriceRow(date(2024, 1, 8), {"T1": Decimal("150")}),
    ]


def _view(
    name: str,
    rendite_pct: float,
    trade_count: int = 10,
    steuer: float = 0.0,
    beitraege: list[dict] | None = None,
    sim_beginn: str = "2024-01-01",
    sim_ende: str = "2024-01-08",
    vergleich_cagr_pct: float | None = None,
) -> dict:
    return {
        "name": name,
        "rendite_pct": rendite_pct,
        "trade_count": trade_count,
        "steuer_num": steuer,
        "startkapital_num": 10000.0,
        "beitraege": beitraege or [],
        # Default: alle Fixtures teilen denselben Zeitraum, damit bestehende
        # Tests weiterhin die "identische Kurshistorie"-Aussage abdecken
        # (siehe _gleiche_kurshistorie() in learnings.py). Ein abweichender
        # Wert simuliert unterschiedlich lange Simulationszeiträume je
        # Strategie (F4/#63).
        "sim_beginn": sim_beginn,
        "sim_ende": sim_ende,
        "vergleich_cagr_pct": vergleich_cagr_pct,
    }


def _titel(learnings) -> list[str]:
    return [l.titel_de for l in learnings]


def test_spannweite_nennt_beste_und_schlechteste_regel():
    views = [_view("Gut", 50.0), _view("Mittel", 10.0), _view("Schlecht", -20.0)]
    learning = next(l for l in derive_learnings(views) if l.titel_de == "Die Regel entscheidet, nicht der Markt")

    # Spread 50 - (-20) = 70 pp
    assert "+70,00 pp" in learning.kernaussage_de
    assert "Gut" in learning.detail_de and "+50,00 %" in learning.detail_de
    assert "Schlecht" in learning.detail_de and "-20,00 %" in learning.detail_de


def test_spannweite_faellt_bei_unterschiedlichen_zeitraeumen_ohne_vergleichszeitraum_weg():
    """Regressionstest: unterschiedlich lange Simulationszeiträume (F4/#63, z. B.
    S&P-500-Benchmark seit ~2006 vs. eine Barbell-Strategie seit ~2021) dürfen die
    Aussage "bei exakt derselben Kurshistorie" nicht erfinden, wenn kein
    gemeinsamer Vergleichszeitraum (#73) vorliegt."""
    views = [
        _view("Benchmark", 706.40, sim_beginn="2006-01-06", sim_ende="2026-08-14"),
        _view("Barbell", 70.96, sim_beginn="2021-11-19", sim_ende="2026-08-14"),
    ]
    assert "Die Regel entscheidet, nicht der Markt" not in _titel(derive_learnings(views))


def test_spannweite_nutzt_vergleichs_cagr_bei_unterschiedlichen_zeitraeumen():
    """Sobald ein gemeinsamer Vergleichszeitraum (#73) verfügbar ist, vergleicht die
    Regel annualisierte CAGR-Werte über diesen gemeinsamen Zeitraum statt der
    Gesamtrendite über je eigene, unterschiedlich lange Zeiträume."""
    views = [
        _view(
            "Benchmark",
            706.40,
            sim_beginn="2006-01-06",
            sim_ende="2026-08-14",
            vergleich_cagr_pct=8.0,
        ),
        _view(
            "Barbell",
            70.96,
            sim_beginn="2021-11-19",
            sim_ende="2026-08-14",
            vergleich_cagr_pct=15.0,
        ),
    ]
    learning = next(l for l in derive_learnings(views) if l.titel_de == "Die Regel entscheidet, nicht der Markt")

    # Spread 15.0 - 8.0 = 7 pp CAGR, nicht 706.40 - 70.96 Gesamtrendite-pp.
    assert "+7,00 pp" in learning.kernaussage_de
    assert "CAGR" in learning.kernaussage_de
    assert "Barbell" in learning.detail_de and "+15,00 pp" in learning.detail_de
    assert "Benchmark" in learning.detail_de and "+8,00 pp" in learning.detail_de
    assert "706,40" not in learning.detail_de and "70,96" not in learning.detail_de


def test_aktivitaet_nennt_platzierung_der_handelsintensivsten_regel():
    views = [
        _view("Vielhandel", -5.0, trade_count=400),
        _view("Mittel", 20.0, trade_count=100),
        _view("Ruhig", 80.0, trade_count=5),
    ]
    learning = next(l for l in derive_learnings(views) if l.titel_de == "Mehr Handeln ist nicht mehr Rendite")

    assert "400 Trades" in learning.kernaussage_de
    assert "Platz 3 von 3" in learning.kernaussage_de
    assert "5 Trades" in learning.kernaussage_de
    assert "Platz 1" in learning.kernaussage_de


def test_aktivitaet_dreht_sich_mit_den_daten():
    """Gegenprobe: handelt die BESTE Regel am meisten, wandert die Platzierung mit."""
    views = [
        _view("Vielhandel", 80.0, trade_count=400),
        _view("Mittel", 20.0, trade_count=100),
        _view("Ruhig", -5.0, trade_count=5),
    ]
    learning = next(l for l in derive_learnings(views) if l.titel_de == "Mehr Handeln ist nicht mehr Rendite")
    assert "Platz 1 von 3" in learning.kernaussage_de


def test_reibungskosten_rechnet_gebuehren_aus_trade_count():
    views = [_view("A", 10.0, trade_count=250, steuer=150.0), _view("B", 5.0, trade_count=3)]
    learning = next(l for l in derive_learnings(views) if l.titel_de.startswith("Reibung"))

    # 250 Trades * 1 EUR + 150 EUR Steuer = 400 EUR = 4,0 % von 10.000 EUR
    assert "400 €" in learning.kernaussage_de
    assert "4,0 %" in learning.kernaussage_de
    assert "250 €" in learning.detail_de and "150 €" in learning.detail_de


def test_kombinationseffekt_zaehlt_negative_beitraege():
    beitraege = [
        {"name": "Regel A", "delta_pp": -30.0},
        {"name": "Regel B", "delta_pp": +12.0},
        {"name": "Regel C", "delta_pp": -4.0},
        {"name": "Regel D", "delta_pp": +3.0},
    ]
    views = [_view("Solo", 40.0), _view("Kombi", -10.0, beitraege=beitraege)]
    learning = next(l for l in derive_learnings(views) if l.titel_de.startswith("Regeln zusammenwerfen"))

    assert "2 von 4" in learning.kernaussage_de
    assert "Regel A" in learning.detail_de and "-30,00 pp" in learning.detail_de
    assert "Regel B" in learning.detail_de and "+12,00 pp" in learning.detail_de
    # Zwei positive Beitraege -> "einziger Rückhalt" waere falsch
    assert "stärkster Rückhalt" in learning.detail_de


def test_kombinationseffekt_formuliert_einzigen_rueckhalt():
    beitraege = [
        {"name": "Regel A", "delta_pp": -30.0},
        {"name": "Regel B", "delta_pp": +12.0},
    ]
    views = [_view("Solo", 40.0), _view("Kombi", -10.0, beitraege=beitraege)]
    learning = next(l for l in derive_learnings(views) if l.titel_de.startswith("Regeln zusammenwerfen"))
    assert "einziger Rückhalt" in learning.detail_de


def test_kombinationseffekt_faellt_ohne_zusammengesetzte_strategie_weg():
    views = [_view("A", 10.0), _view("B", 20.0), _view("C", 30.0)]
    assert not any(t.startswith("Regeln zusammenwerfen") for t in _titel(derive_learnings(views)))


def test_learnings_fallen_bei_zu_wenigen_strategien_still_weg():
    """Eine einzelne Strategie beantwortet keine der Vergleichsfragen."""
    learnings = derive_learnings([_view("Einzeln", 10.0, trade_count=5)])
    assert "Die Regel entscheidet, nicht der Markt" not in _titel(learnings)
    assert "Mehr Handeln ist nicht mehr Rendite" not in _titel(learnings)


def test_derive_learnings_mit_leerer_liste():
    assert derive_learnings([]) == []


def test_build_dashboard_rendert_learnings_sektion(tmp_path: Path):
    zwei = [_einfache_strategie("A: Verdoppler"), _einfache_strategie("B: Zwilling")]
    output = build_dashboard(_rows(), zwei, output_path=tmp_path / "index.html")
    html = output.read_text(encoding="utf-8")

    assert 'id="key-learnings"' in html
    assert "Key Learnings" in html
    # Beide Test-Strategien sind identisch -> Spannweite 0, aber die Sektion steht.
    assert "Die Regel entscheidet, nicht der Markt" in html


def test_build_dashboard_ohne_learnings_rendert_keine_leere_sektion(tmp_path: Path):
    """Eine einzelne, handelsfreie Strategie erzeugt kein einziges Learning."""
    output = build_dashboard(
        _rows(), [_einfache_strategie("Nur eine")], output_path=tmp_path / "index.html"
    )
    html = output.read_text(encoding="utf-8")

    assert 'id="key-learnings"' not in html
