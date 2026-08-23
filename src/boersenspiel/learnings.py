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

Jede Regel liefert ihre drei Textfelder zweisprachig (``*_de``/``*_en``) - das
Dashboard rendert Deutsch als Standard und blendet Englisch per
data-i18n-en-Attribut clientseitig ein (Dreipunktmenü-Sprachumschalter).
"""

from __future__ import annotations

from dataclasses import dataclass

from .strategies import ORDERGEBUEHR


@dataclass(frozen=True)
class Learning:
    titel_de: str
    titel_en: str
    kernaussage_de: str  # eine Zeile, die Zahl im Vordergrund
    kernaussage_en: str
    detail_de: str  # Einordnung/Begründung
    detail_en: str


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


def _zahl_en(wert: float, nachkommastellen: int) -> str:
    return f"{wert:.{nachkommastellen}f}"


def _pp_en(wert: float) -> str:
    return f"{_zahl_en(wert, 2)} pp" if wert < 0 else f"+{_zahl_en(wert, 2)} pp"


def _pct_en(wert: float) -> str:
    return f"{_zahl_en(wert, 2)}%" if wert < 0 else f"+{_zahl_en(wert, 2)}%"


def _eur_en(wert: float) -> str:
    return f"€{wert:,.0f}"


def _spannweite(views: list[dict]) -> Learning | None:
    """Wie viel hängt überhaupt an der Regel - bei identischer Kurshistorie?"""
    if len(views) < 2:
        return None
    rangliste = _nach_rendite(views)
    bester, schlechtester = rangliste[0], rangliste[-1]
    spread = bester["rendite_pct"] - schlechtester["rendite_pct"]
    return Learning(
        titel_de="Die Regel entscheidet, nicht der Markt",
        titel_en="The rule decides, not the market",
        kernaussage_de=(
            f"{_pp(spread)} Unterschied zwischen bester und schlechtester Regel - "
            f"bei exakt derselben Kurshistorie."
        ),
        kernaussage_en=(
            f"{_pp_en(spread)} difference between the best and worst rule - "
            f"on exactly the same price history."
        ),
        detail_de=(
            f"„{bester['name']}\" kommt auf {_pct(bester['rendite_pct'])}, "
            f"„{schlechtester['name']}\" auf {_pct(schlechtester['rendite_pct'])}. "
            f"Alle {len(views)} Läufe sehen dieselben Kurse, dasselbe Startkapital und "
            f"dieselbe Steuer-/Gebührenlogik. Der gesamte Unterschied entsteht allein "
            f"daraus, wann umgeschichtet wird."
        ),
        detail_en=(
            f"\"{bester['name']}\" reaches {_pct_en(bester['rendite_pct'])}, "
            f"\"{schlechtester['name']}\" reaches {_pct_en(schlechtester['rendite_pct'])}. "
            f"All {len(views)} runs see the same prices, the same starting capital and "
            f"the same tax/fee logic. The entire difference comes purely from when the "
            f"portfolio gets reshuffled."
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
        titel_de="Mehr Handeln ist nicht mehr Rendite",
        titel_en="More trading isn't more return",
        kernaussage_de=(
            f"Die handelsintensivste Regel ({aktivste['trade_count']} Trades) landet auf "
            f"Platz {platz[aktivste['name']]} von {len(views)}, die ruhigste "
            f"({ruhigste['trade_count']} Trades) auf Platz {platz[ruhigste['name']]}."
        ),
        kernaussage_en=(
            f"The most trade-intensive rule ({aktivste['trade_count']} trades) ends up in "
            f"rank {platz[aktivste['name']]} of {len(views)}, the quietest one "
            f"({ruhigste['trade_count']} trades) in rank {platz[ruhigste['name']]}."
        ),
        detail_de=(
            f"„{aktivste['name']}\" handelt am meisten und erreicht "
            f"{_pct(aktivste['rendite_pct'])}; „{ruhigste['name']}\" handelt am wenigsten "
            f"und erreicht {_pct(ruhigste['rendite_pct'])}. Jeder Trade kostet "
            f"{ORDERGEBUEHR} € Gebühr und kann zusätzlich Gewinne steuerpflichtig "
            f"realisieren - Aktivität muss diesen Aufschlag erst wieder verdienen."
        ),
        detail_en=(
            f"\"{aktivste['name']}\" trades the most and reaches "
            f"{_pct_en(aktivste['rendite_pct'])}; \"{ruhigste['name']}\" trades the least "
            f"and reaches {_pct_en(ruhigste['rendite_pct'])}. Every trade costs "
            f"€{ORDERGEBUEHR} in fees and can additionally trigger taxable realized "
            f"gains - activity first has to earn back that surcharge."
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
        titel_de="Reibung ist sichtbar, aber selten entscheidend",
        titel_en="Friction is visible, but rarely decisive",
        kernaussage_de=(
            f"{_eur(gesamt)} Gebühren und Steuer bei „{aktivste['name']}\" - "
            f"{_zahl(anteil, 1)} % des Startkapitals."
        ),
        kernaussage_en=(
            f"{_eur_en(gesamt)} in fees and tax for \"{aktivste['name']}\" - "
            f"{_zahl_en(anteil, 1)}% of the starting capital."
        ),
        detail_de=(
            f"Davon {_eur(gebuehren)} Ordergebühren ({aktivste['trade_count']} Trades × "
            f"{ORDERGEBUEHR} €) und {_eur(steuer)} kumulierte Steuer. Das erklärt einen "
            f"Teil des Rückstands aktiver Regeln, aber nicht die großen Abstände in der "
            f"Übersicht - die entstehen dadurch, zu welchen Kursen umgeschichtet wird, "
            f"nicht durch die Transaktionskosten."
        ),
        detail_en=(
            f"Of that, {_eur_en(gebuehren)} order fees ({aktivste['trade_count']} trades × "
            f"€{ORDERGEBUEHR}) and {_eur_en(steuer)} cumulative tax. That explains part "
            f"of the gap for active rules, but not the large gaps in the overview - "
            f"those come from the prices at which the portfolio gets reshuffled, not "
            f"from transaction costs."
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
    rueckhalt_de = "einziger Rückhalt" if len(negativ) == len(beitraege) - 1 else "stärkster Rückhalt"
    rueckhalt_en = "sole positive contributor" if len(negativ) == len(beitraege) - 1 else "strongest positive contributor"
    return Learning(
        titel_de="Regeln zusammenwerfen macht sie nicht besser",
        titel_en="Throwing rules together doesn't make them better",
        kernaussage_de=(
            f"„{kombi['name']}\" kommt auf {_pct(kombi['rendite_pct'])}; "
            f"{len(negativ)} von {len(beitraege)} Teilregeln liefern einen negativen "
            f"Beitrag."
        ),
        kernaussage_en=(
            f"\"{kombi['name']}\" reaches {_pct_en(kombi['rendite_pct'])}; "
            f"{len(negativ)} of {len(beitraege)} sub-rules contribute a negative "
            f"effect."
        ),
        detail_de=(
            f"Größter Bremsklotz ist „{schlechtester['name']}\" mit "
            f"{_pp(schlechtester['delta_pp'])}, "
            f"{rueckhalt_de} "
            f"„{bester['name']}\" mit {_pp(bester['delta_pp'])}. Sich widersprechende "
            f"Signale erzeugen Hin- und Her-Umschichtungen (Whipsaw): erst nach einem "
            f"Rückgang verkaufen, dann nach der Erholung zurückkaufen. Die Einzeleffekte "
            f"stehen im Detailabschnitt der Strategie."
        ),
        detail_en=(
            f"The biggest drag is \"{schlechtester['name']}\" at "
            f"{_pp_en(schlechtester['delta_pp'])}, the "
            f"{rueckhalt_en} is "
            f"\"{bester['name']}\" at {_pp_en(bester['delta_pp'])}. Contradicting "
            f"signals produce back-and-forth reshuffling (whipsaw): selling after a "
            f"decline, then buying back after the recovery. The individual effects are "
            f"listed in the strategy's detail section."
        ),
    )


def _extremwerte_streuung(views: list[dict]) -> Learning | None:
    """Wie viele Regeln liegen überhaupt im Plus?"""
    if len(views) < 3:
        return None
    positiv = [v for v in views if v["rendite_pct"] > 0]
    anteil = len(positiv) / len(views) * 100
    return Learning(
        titel_de="Ein Zeitfenster ist kein Beweis",
        titel_en="One time window is not proof",
        kernaussage_de=(
            f"{len(positiv)} von {len(views)} Regeln stehen im Plus "
            f"({_zahl(anteil, 0)} %) - über genau einen historischen Zeitraum."
        ),
        kernaussage_en=(
            f"{len(positiv)} of {len(views)} rules are in the black "
            f"({_zahl_en(anteil, 0)}%) - over exactly one historical period."
        ),
        detail_de=(
            "Die Auswertung läuft über einen einzigen historischen Zeitraum und über "
            "Regeln, deren Parameter bewusst nicht optimiert wurden. Sie zeigt, wie sich "
            "diese Regeln in genau diesem Fenster verhalten hätten - kein Beleg dafür, "
            "wie sie sich künftig verhalten, und keine Anlageempfehlung."
        ),
        detail_en=(
            "This evaluation runs over a single historical period, and over rules whose "
            "parameters were deliberately not optimized. It shows how these rules would "
            "have behaved in exactly this window - not evidence of how they will behave "
            "in the future, and not investment advice."
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
