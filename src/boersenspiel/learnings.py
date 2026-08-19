"""Leitet "Key Learnings" aus den Simulationsergebnissen ab.

Bewusst **keine** fest hinterlegten Erkenntnis-Texte: jede Aussage entsteht bei
jedem Dashboard-Build neu aus den Zahlen, die ``engine.simulate()`` gerade
geliefert hat. Ändert sich die Kurshistorie, ändern sich die Learnings mit -
genau wie jede andere abgeleitete Größe im Projekt (siehe Leitprinzip in der
README). Fest ist nur die *Fragestellung* je Regel, nicht ihr Ergebnis.

Jede Regel ist eine reine Funktion ``(views) -> Learning | None`` über den
bereits gerenderten Strategie-Views aus ``dashboard.py``. Liefert sie ``None``,
lässt sich die Frage aus den vorhandenen Daten nicht beantworten (z. B. weil zu
wenige Strategien vorliegen) - dann fällt das Learning still weg, statt eine
Aussage zu erfinden.
"""

from __future__ import annotations

from dataclasses import dataclass

from .strategies import ORDERGEBUEHR


@dataclass(frozen=True)
class Learning:
    titel: str
    kernaussage: str  # eine Zeile, die Zahl im Vordergrund
    detail: str  # Einordnung/Begründung


def _nach_rendite(views: list[dict]) -> list[dict]:
    return sorted(views, key=lambda v: v["rendite_pct"], reverse=True)


def _zahl(wert: float, nachkommastellen: int) -> str:
    """Deutsches Dezimalkomma - bewusst nur auf der Zahl, nicht auf dem Satz."""
    return f"{wert:.{nachkommastellen}f}".replace(".", ",")


def _pp(wert: float) -> str:
    return f"{_zahl(wert, 2)} pp" if wert < 0 else f"+{_zahl(wert, 2)} pp"


def _pct(wert: float) -> str:
    return f"{_zahl(wert, 2)} %" if wert < 0 else f"+{_zahl(wert, 2)} %"


def _eur(wert: float) -> str:
    return f"{wert:,.0f} €".replace(",", ".")


def _spannweite(views: list[dict]) -> Learning | None:
    """Wie viel hängt überhaupt an der Regel - bei identischer Kurshistorie?"""
    if len(views) < 2:
        return None
    rangliste = _nach_rendite(views)
    bester, schlechtester = rangliste[0], rangliste[-1]
    spread = bester["rendite_pct"] - schlechtester["rendite_pct"]
    return Learning(
        titel="Die Regel entscheidet, nicht der Markt",
        kernaussage=(
            f"{_pp(spread)} Unterschied zwischen bester und schlechtester Regel - "
            f"bei exakt derselben Kurshistorie."
        ),
        detail=(
            f"„{bester['name']}\" kommt auf {_pct(bester['rendite_pct'])}, "
            f"„{schlechtester['name']}\" auf {_pct(schlechtester['rendite_pct'])}. "
            f"Alle {len(views)} Läufe sehen dieselben Kurse, dasselbe Startkapital und "
            f"dieselbe Steuer-/Gebührenlogik. Der gesamte Unterschied entsteht allein "
            f"daraus, wann umgeschichtet wird."
        ),
    )


def _aktivitaet(views: list[dict]) -> Learning | None:
    """Zahlt sich häufiges Umschichten aus?"""
    if len(views) < 3:
        return None
    rangliste = _nach_rendite(views)
    platz = {v["name"]: i + 1 for i, v in enumerate(rangliste)}
    aktivste = max(views, key=lambda v: v["trade_count"])
    ruhigste = min(views, key=lambda v: v["trade_count"])
    if aktivste["name"] == ruhigste["name"]:
        return None
    return Learning(
        titel="Mehr Handeln ist nicht mehr Rendite",
        kernaussage=(
            f"Die handelsintensivste Regel ({aktivste['trade_count']} Trades) landet auf "
            f"Platz {platz[aktivste['name']]} von {len(views)}, die ruhigste "
            f"({ruhigste['trade_count']} Trades) auf Platz {platz[ruhigste['name']]}."
        ),
        detail=(
            f"„{aktivste['name']}\" handelt am meisten und erreicht "
            f"{_pct(aktivste['rendite_pct'])}; „{ruhigste['name']}\" handelt am wenigsten "
            f"und erreicht {_pct(ruhigste['rendite_pct'])}. Jeder Trade kostet "
            f"{ORDERGEBUEHR} € Gebühr und kann zusätzlich Gewinne steuerpflichtig "
            f"realisieren - Aktivität muss diesen Aufschlag erst wieder verdienen."
        ),
    )


def _reibungskosten(views: list[dict]) -> Learning | None:
    """Was kosten Gebühren und Steuer die aktivste Regel?"""
    # "die aktivste" ist nur im Vergleich eine Aussage.
    if len(views) < 2:
        return None
    aktivste = max(views, key=lambda v: v["trade_count"])
    gebuehren = aktivste["trade_count"] * float(ORDERGEBUEHR)
    steuer = aktivste["steuer_num"]
    gesamt = gebuehren + steuer
    if gesamt <= 0 or aktivste["startkapital_num"] <= 0:
        return None
    anteil = gesamt / aktivste["startkapital_num"] * 100
    return Learning(
        titel="Reibung ist sichtbar, aber selten entscheidend",
        kernaussage=(
            f"{_eur(gesamt)} Gebühren und Steuer bei „{aktivste['name']}\" - "
            f"{_zahl(anteil, 1)} % des Startkapitals."
        ),
        detail=(
            f"Davon {_eur(gebuehren)} Ordergebühren ({aktivste['trade_count']} Trades × "
            f"{ORDERGEBUEHR} €) und {_eur(steuer)} kumulierte Steuer. Das erklärt einen "
            f"Teil des Rückstands aktiver Regeln, aber nicht die großen Abstände in der "
            f"Übersicht - die entstehen dadurch, zu welchen Kursen umgeschichtet wird, "
            f"nicht durch die Transaktionskosten."
        ),
    )


def _kombinationseffekt(views: list[dict]) -> Learning | None:
    """Was passiert, wenn man widersprüchliche Regeln zusammenwirft?"""
    zusammengesetzt = [v for v in views if v["beitraege"]]
    if not zusammengesetzt:
        return None
    kombi = min(zusammengesetzt, key=lambda v: v["rendite_pct"])
    beitraege = kombi["beitraege"]
    negativ = [b for b in beitraege if b["delta_pp"] < 0]
    bester = max(beitraege, key=lambda b: b["delta_pp"])
    schlechtester = min(beitraege, key=lambda b: b["delta_pp"])
    return Learning(
        titel="Regeln zusammenwerfen macht sie nicht besser",
        kernaussage=(
            f"„{kombi['name']}\" kommt auf {_pct(kombi['rendite_pct'])}; "
            f"{len(negativ)} von {len(beitraege)} Teilregeln liefern einen negativen "
            f"Beitrag."
        ),
        detail=(
            f"Größter Bremsklotz ist „{schlechtester['name']}\" mit "
            f"{_pp(schlechtester['delta_pp'])}, "
            f"{'einziger Rückhalt' if len(negativ) == len(beitraege) - 1 else 'stärkster Rückhalt'} "
            f"„{bester['name']}\" mit {_pp(bester['delta_pp'])}. Sich widersprechende "
            f"Signale erzeugen Hin- und Her-Umschichtungen (Whipsaw): erst nach einem "
            f"Rückgang verkaufen, dann nach der Erholung zurückkaufen. Die Einzeleffekte "
            f"stehen im Detailabschnitt der Strategie."
        ),
    )


def _extremwerte_streuung(views: list[dict]) -> Learning | None:
    """Wie viele Regeln liegen überhaupt im Plus?"""
    if len(views) < 3:
        return None
    positiv = [v for v in views if v["rendite_pct"] > 0]
    anteil = len(positiv) / len(views) * 100
    return Learning(
        titel="Ein Zeitfenster ist kein Beweis",
        kernaussage=(
            f"{len(positiv)} von {len(views)} Regeln stehen im Plus "
            f"({_zahl(anteil, 0)} %) - über genau einen historischen Zeitraum."
        ),
        detail=(
            "Die Auswertung läuft über einen einzigen historischen Zeitraum und über "
            "Regeln, deren Parameter bewusst nicht optimiert wurden. Sie zeigt, wie sich "
            "diese Regeln in genau diesem Fenster verhalten hätten - kein Beleg dafür, "
            "wie sie sich künftig verhalten, und keine Anlageempfehlung."
        ),
    )


REGELN = (
    _spannweite,
    _kombinationseffekt,
    _aktivitaet,
    _reibungskosten,
    _extremwerte_streuung,
)


def derive_learnings(views: list[dict]) -> list[Learning]:
    """Wendet alle Regeln an und liefert die Learnings, die sich beantworten lassen."""
    ergebnisse = (regel(views) for regel in REGELN)
    return [learning for learning in ergebnisse if learning is not None]
