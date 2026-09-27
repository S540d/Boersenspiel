# Incidents

Vorfallsarchiv für dieses Projekt: konkrete Bugs/Fehlfunktionen, die
gefunden und behoben wurden, mit voller Diagnose-Erzählung (Datum/Issue,
betroffene Dateien, Beispielzahlen). `CLAUDE.md` verweist von der jeweils
betroffenen Stelle hierher und enthält nur noch die Kernregel plus einen
kurzen Auslöser-Kontext (Wartungsregel siehe `CLAUDE.md`, Abschnitt
"Wartung").

Eingeführt im Zuge von project-templates#160 (CLAUDE.md-Wartungsregel:
Incident-Details raus, nur Kernregel + Link bleibt). Erster Cleanup-Pass,
noch nicht vollständig — CLAUDE.md enthält daneben viele Abschnitte, die
keine Vorfälle, sondern laufende Architektur-/Modellierungsentscheidungen
sind (siehe README.md-Hinweis in CLAUDE.md); die bleiben bewusst dort.

## ibci-exxy-ausschuettend — Falsch markierte Steuerattribute (#64)

Beim Ergänzen der sieben #64-Instrumente wurden die Steuerattribute
(`teilfreistellung`/`thesaurierend`/`ausschuettend`) gegen öffentliche
Fondsanbieter-Fact-Sheets (justETF/extraETF/onvista/DAS INVESTMENT)
verifiziert. Dabei zeigte sich: `IBCI` und `EXXY` sind tatsächlich
thesaurierende Acc-Anteilsklassen, standen in `instruments.py` aber
zunächst fälschlich als `ausschuettend=True`. Relevant, weil beide
Instrumente inzwischen tatsächlich alloziert sind (`SP500_BENCHMARK`/
`BARBELL_20_80_DIVERSIFIZIERT`) und die falsche Markierung die
Vorabpauschale-/Dividendenmodellierung verfälscht hätte.

**Fix:** beide auf `ausschuettend=False` korrigiert. Regressionstest:
`tests/test_neue_strategien.py`, prüft `IBCI`/`EXXY` explizit als
thesaurierend.

## zielgewicht-trigger — Rebalancing-Trigger sah Ziel-Verschiebung innerhalb eines Topfs nicht (Reaktion auf eine Projektprüfung, ergänzt #63)

Der bestehende Rebalancing-Trigger vergleicht Ist- gegen Ziel-Gewicht nur
je Topf. Eine `gewichte_fn` (siehe `scenarios.py`), die nur INNERHALB
eines Topfs umschichtet (Topf-Summe bleibt gleich), löste dadurch NIE ein
Rebalancing aus, weil das „Ziel" pro Zeile direkt aus der bereits
verschobenen `current_weights` abgeleitet wurde — Topf-Ist und Topf-Ziel
stimmten also immer überein.

Betroffen war vor allem das Momentum-Szenario (`scenarios.py`,
Top-2-Rotation der Wachstums-Instrumente): über den vollen
Vergleichszeitraum löste NUR der initiale Erstkauf ein einziges Mal aus,
danach folgte der simulierte Wertverlauf bis auf einen einzigen Handelstag
am Ende exakt dem zugrundeliegenden Barbell-Portfolio — obwohl die Regel
wöchentlich sieben verschiedene Ziel-Gewichtsvektoren berechnete (u. a.
40% Bitcoin + 40% Halbleiter-ETF). Die ausgewiesene Rotation hatte in den
Zahlen schlicht nie stattgefunden.

**Fix:** `simulate()` führt zusätzlich `letzte_ziel_gewichte` und prüft
vor dem Topf-Trigger für jedes Instrument dieselbe 5/25-Schwelle gegen die
Differenz aus `current_weights` und `letzte_ziel_gewichte` (Details siehe
`engine.py` in CLAUDE.md). Regressionstest:
`tests/test_engine.py::test_zielgewicht_aenderung_innerhalb_eines_topfs_loest_rebalancing_aus`.

## werterhaltung-rebalancing — Rebalancing ließ Depotwert wöchentlich schrumpfen

`rebalance_to_targets()` führt kein Cash-Konto — die Umschichtung ist
allein dadurch summenneutral, dass sich die `diffs` über *alle*
Instrumente zu genau dem vorhandenen `pending_cash` aufaddieren. Ein
Instrument ohne Kurs in der aktuellen Zeile wurde beim Rebalancing
zunächst einfach übersprungen, statt seinen Zielanteil als `pending_cash`
zu parken. Das zerstörte die Summenneutralität: Geld verschwand
ersatzlos.

**Effekt vor dem Fix:** alle rebalancierenden Strategien schrumpften über
die lange Historie wöchentlich um ~50%, bis der Depotwert praktisch bei
0 EUR ankam. Nur `BUY_AND_HOLD` blieb korrekt, weil es nie rebalanciert.

**Fix:** Instrumente ohne aktuellen Kurs parken ihren Zielanteil als
`pending_cash` (dieselbe Mechanik wie beim Initialkauf,
`delayed_initial_buy`). Regressionstests in `tests/test_engine.py`.

## verdecktes-vollrebalancing — Börsengang eines neuen Instruments löste ungewollt volles Rebalancing aus (#62)

Bei ausgeschaltetem Rebalancing (`opt.rebalancing=False`) sollte der
Erstkauf eines neu handelbar gewordenen Instruments (z. B. ein
Aktien-IPO) nur dessen eigenes Zielgewicht kaufen und den restlichen
Bestand unangetastet lassen. Stattdessen lief `erstkauf_gewichte()` bei
jedem Börsengang ein komplettes Rebalancing über `current_weights` —
über die volle 20-Jahres-Historie 27 verdeckte Voll-Rebalancings. Folge:
„Time in the market beats timing the market" (`BUY_AND_HOLD`) lieferte
bit-identische 1-/3-/5-Jahres-Renditen wie die rebalancierende
Barbell-Strategie, obwohl es laut Definition nie rebalancieren sollte.

**Fix:** `erstkauf_gewichte()` gibt dem neuen Instrument sein reguläres
Zielgewicht und lässt den übrigen Bestand nur proportional
herunterskaliert in seinen *aktuellen* Marktwert-Verhältnissen (siehe
`engine.py` in CLAUDE.md für die vollständige Erklärung des aktuellen
Verhaltens). Regressionstest in `tests/test_engine.py` (Drei-Instrumente-
Strategie mit ungleichen Zielgewichten, ein Instrument kommt später an
den Markt).

## splitbereinigung — Aktiensplits sahen im Backfill wie Kursstürze aus (#62)

Der Backfill lief zunächst über `TIME_SERIES_WEEKLY`, also über
*nominale* Schlusskurse. Jeder Aktiensplit sieht dort wie ein Kurssturz
aus — in der 20-Jahres-Historie betraf das fünf der zehn
Satelliten-Aktien mit Phantom-Wochenverlusten bis -91%: TSLA (5:1 in
2020, 3:1 in 2022), MSTR (10:1 in 2024), KO (2:1 in 2012), RHHBY und
BYDDY (je ein ADR-Verhältniswechsel).

**Fix:** Backfill nutzt seither `TIME_SERIES_WEEKLY_ADJUSTED`;
`_split_bereinigte_close_series()` leitet aus `close / adjusted close`
den kumulierten Split-Faktor ab und teilt die Nominalkurse dadurch (nicht
den vollen `adjusted close`, der auch Dividenden enthält und diese
doppelt gezählt hätte — Details siehe `scripts/backfill_history.py` in
CLAUDE.md). Regressionstests in `tests/test_alphavantage.py` (u. a.
nachgebaute TSLA-Reihe mit beiden Splits).

## fx-rueckwaerts-extrapolation — Wechselkurs vor 2014 fälschlich mit dem Kurs von 2014 umgerechnet (#62)

`_nearest_fx_rate()` fiel für Wochen *vor* Beginn der `FX_WEEKLY`-Reihe
(Alpha Vantage liefert USD/EUR erst ab November 2014) auf den ältesten
verfügbaren Kurs zurück — der aber jünger ist als das umzurechnende
Datum. Dadurch wurden im 20-Jahres-Backfill 227 Wochen (Juli 2010 bis
November 2014) aller neun USD-Ticker sowie die komplette frühe
BTC-Historie mit dem konstanten Kurs 0,7982 EUR/USD von 2014 umgerechnet
— die tatsächliche Wechselkursbewegung dieser Jahre fehlte vollständig.

**Fix:** `_nearest_fx_rate()` liefert für solche Wochen `None`,
`record_week()` trägt „missing" ein, `_fx_luecken()` meldet jeden
Zeitraum ohne Abdeckung (Details siehe `scripts/backfill_history.py` in
CLAUDE.md). Die 2.272 handgepflegten EZB-Referenzkurse in
`data/manual_fx_usd_eur.csv` (2006-01-02 bis 2014-11-14) schließen die
dadurch entstehende Lücke wieder.

## fx-weekly-schluessel — Backfill-Lauf brach durch falsch angenommenen Zeitreihen-Schlüssel ab

Lauf vom 18.08.2026: alle drei Alpha-Vantage-Endpunkte
(`TIME_SERIES_WEEKLY`/`FX_WEEKLY`/`DIGITAL_CURRENCY_WEEKLY`) waren im
Free-Tier verfügbar und alle 17 Symbol-Mappings lösten auf, der Lauf
brach aber trotzdem ab: der Code nahm einen festen Zeitreihen-Schlüssel
in der JSON-Antwort an, Alpha Vantage benennt ihn aber je Endpunkt
unterschiedlich (`Weekly Time Series` / `Time Series FX (Weekly)` /
`Time Series (Digital Currency Weekly)`).

**Fix:** `_extract_time_series()` rät den Namen nicht mehr, sondern
nimmt den einzigen Objekt-Wert der Antwort außer `Meta Data` — Fehler-
und Rate-Limit-Antworten haben nur String-Werte und lösen damit
automatisch eine aussagekräftige Exception aus.

## commit-merge-konflikt — Zeitgleicher Merge auf main blockierte Pages-Deploy

Beobachtet am 22.08.2026, Workflow-Lauf #16 von
`.github/workflows/weekly-update.yml`: Fetch und Dashboard-Build liefen
beide durch, ein zeitgleicher Merge auf `main` ließ aber den `git push`
der Kurshistorie in einen echten Merge-Konflikt laufen. Ohne
`continue-on-error` am Commit-Schritt riss das den gesamten Job ab und
übersprang dadurch auch „Pages-Artefakt hochladen" sowie den
`deploy`-Job — ein für den Pages-Deploy irrelevanter git-Konflikt
verhinderte so die Veröffentlichung eines bereits fertig gebauten
Dashboards.

**Fix:** Commit-Schritt trägt seither `continue-on-error: true`.
`record_week()` ist wochen-idempotent, ein bei einem Konflikt verpasster
Commit holt sich beim nächsten erfolgreichen Lauf von selbst nach — es
geht keine Kurswoche verloren, nur die Veröffentlichung eines einzelnen
Laufs wäre sonst unnötig blockiert gewesen.
