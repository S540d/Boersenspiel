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
) -> dict:
    return {
        "name": name,
        "rendite_pct": rendite_pct,
        "trade_count": trade_count,
        "steuer_num": steuer,
        "startkapital_num": 10000.0,
        "beitraege": beitraege or [],
    }


def _titel(learnings) -> list[str]:
    return [l.titel for l in learnings]


def test_spannweite_nennt_beste_und_schlechteste_regel():
    views = [_view("Gut", 50.0), _view("Mittel", 10.0), _view("Schlecht", -20.0)]
    learning = next(l for l in derive_learnings(views) if l.titel == "Die Regel entscheidet, nicht der Markt")

    # Spread 50 - (-20) = 70 pp
    assert "+70,00 pp" in learning.kernaussage
    assert "Gut" in learning.detail and "+50,00 %" in learning.detail
    assert "Schlecht" in learning.detail and "-20,00 %" in learning.detail


def test_aktivitaet_nennt_platzierung_der_handelsintensivsten_regel():
    views = [
        _view("Vielhandel", -5.0, trade_count=400),
        _view("Mittel", 20.0, trade_count=100),
        _view("Ruhig", 80.0, trade_count=5),
    ]
    learning = next(l for l in derive_learnings(views) if l.titel == "Mehr Handeln ist nicht mehr Rendite")

    assert "400 Trades" in learning.kernaussage
    assert "Platz 3 von 3" in learning.kernaussage
    assert "5 Trades" in learning.kernaussage
    assert "Platz 1" in learning.kernaussage


def test_aktivitaet_dreht_sich_mit_den_daten():
    """Gegenprobe: handelt die BESTE Regel am meisten, wandert die Platzierung mit."""
    views = [
        _view("Vielhandel", 80.0, trade_count=400),
        _view("Mittel", 20.0, trade_count=100),
        _view("Ruhig", -5.0, trade_count=5),
    ]
    learning = next(l for l in derive_learnings(views) if l.titel == "Mehr Handeln ist nicht mehr Rendite")
    assert "Platz 1 von 3" in learning.kernaussage


def test_reibungskosten_rechnet_gebuehren_aus_trade_count():
    views = [_view("A", 10.0, trade_count=250, steuer=150.0), _view("B", 5.0, trade_count=3)]
    learning = next(l for l in derive_learnings(views) if l.titel.startswith("Reibung"))

    # 250 Trades * 1 EUR + 150 EUR Steuer = 400 EUR = 4,0 % von 10.000 EUR
    assert "400 €" in learning.kernaussage
    assert "4,0 %" in learning.kernaussage
    assert "250 €" in learning.detail and "150 €" in learning.detail


def test_kombinationseffekt_zaehlt_negative_beitraege():
    beitraege = [
        {"name": "Regel A", "delta_pp": -30.0},
        {"name": "Regel B", "delta_pp": +12.0},
        {"name": "Regel C", "delta_pp": -4.0},
        {"name": "Regel D", "delta_pp": +3.0},
    ]
    views = [_view("Solo", 40.0), _view("Kombi", -10.0, beitraege=beitraege)]
    learning = next(l for l in derive_learnings(views) if l.titel.startswith("Regeln zusammenwerfen"))

    assert "2 von 4" in learning.kernaussage
    assert "Regel A" in learning.detail and "-30,00 pp" in learning.detail
    assert "Regel B" in learning.detail and "+12,00 pp" in learning.detail
    # Zwei positive Beitraege -> "einziger Rückhalt" waere falsch
    assert "stärkster Rückhalt" in learning.detail


def test_kombinationseffekt_formuliert_einzigen_rueckhalt():
    beitraege = [
        {"name": "Regel A", "delta_pp": -30.0},
        {"name": "Regel B", "delta_pp": +12.0},
    ]
    views = [_view("Solo", 40.0), _view("Kombi", -10.0, beitraege=beitraege)]
    learning = next(l for l in derive_learnings(views) if l.titel.startswith("Regeln zusammenwerfen"))
    assert "einziger Rückhalt" in learning.detail


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
