# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Virtuelles Portfolio-Dashboard nach einer Barbell-Strategie, basierend auf
einem ursprünglichen Anforderungsdokument aus der frühen Planungsphase
(nicht Teil dieses Repos). Wöchentlicher Kursabruf via GitHub Actions, Kurshistorie als CSV im Repo,
statisches Dashboard (Chart.js) auf GitHub Pages. Default-Branch ist `main`
(ursprünglich hieß er `claude/pflichtenheft-umsetzung-planen-6kf05s`, da das
Repo leer angelegt wurde, und wurde nachträglich zu `main` umbenannt).

**README.md ist ein technisches Referenzdokument** (kurzzeitig im Zuge von
#64-Nachfolgearbeit zu einem kurzen Marketing-/Onboarding-Dokument
umgeschrieben, auf Wunsch des Owners aber wieder auf die ausführliche
technische Fassung zurückgesetzt): Architektur-Diagramm, Engine-
Modellierungsentscheidungen, volle Steuerlogik, Szenario-Tabellen und
bekannte Einschränkungen stehen direkt im README, nicht nur verlinkt auf die
Dashboard-Seiten. Ein `## Portfolio overview`-Abschnitt listet alle 26
Instrumente mit der Strategie, die sie tatsächlich hält. Die Ableitung aus
dem ursprünglichen Anforderungsdokument (Pflichtenheft) wurde auf
Owner-Wunsch aus dem README gestrichen — das Dokument ist ohnehin nicht Teil
dieses Repos und für externe Leser nicht nachprüfbar; die wenigen Stellen, die
zuvor explizit "requirements document"/"Pflichtenheft" zitiert hatten (Tax-
Logic-Absatz, Guiding-Principle-Überschrift, "Adding a strategy"), wurden neutral
umformuliert statt die Aussage selbst zu streichen.

## Commands

```bash
pip install -r requirements.txt

pytest -q                          # gesamte Testsuite
pytest tests/test_engine.py -q     # einzelne Testdatei
pytest tests/test_engine.py::test_simple_strategy_end_to_end_exact_values -q  # einzelner Test

python scripts/run_fetch.py --batch 1               # Kursabruf via Alpha Vantage, Batch 1 (benötigt ALPHAVANTAGE_API_KEY env var)
python scripts/run_fetch.py --batch 2               # Batch 2, am Folgetag (zusammen decken beide alle Ticker ab, #99)
python scripts/backfill_history.py --years 20 --batch 1   # historischer Backfill Tag 1 (ersetzt price_history.csv)
python scripts/backfill_history.py --years 20 --batch 2   # Tag 2, mischt additiv dazu (27 Requests > Tageslimit 25)
python scripts/build_dashboard.py                  # baut docs/index.html aus data/price_history.csv (Strategien + Szenarien)
python scripts/build_dashboard.py --strategy "Barbell 20/80"  # nur eine Strategie/ein Szenario rendern
```

Kein Lint-/Format-Tooling konfiguriert; `pytest.ini` setzt `pythonpath = src`,
sodass `boersenspiel` ohne Installation importierbar ist.

## Architektur

```
Kursquelle (austauschbar) --> data/price_history.csv --> engine.simulate() --> docs/index.html
                                       ^                        |
                                data/fetch_log.csv        (pro Strategie)
```

**Leitprinzip:** Nur Rohdaten (Kurse) werden dauerhaft gespeichert. Alles
Abgeleitete (Positionswerte, Rebalancing, Steuer, Freibetrag, Verlustvortrag)
wird bei jeder Dashboard-Erzeugung komplett neu aus der Kurshistorie
berechnet — `engine.simulate(price_history, strategy)` ist eine **reine
Funktion** ohne eigenen Zustand, kein I/O, kein `datetime.now()`. Das
garantiert Determinismus: identische Kurshistorie + identische Strategie
ergeben immer dasselbe Ergebnis, bei jedem Aufruf neu simuliert (nicht
inkrementell fortgeschrieben).

### Trennung der Verantwortlichkeiten (wichtig beim Erweitern)

- `instruments.py` — die 26 Instrumente (7 Barbell-Basisinstrumente + 10
  Einzelaktien-Satellit + 7 im Zuge von #64 ergänzte Instrumente + 2 aus #99),
  **quellenunabhängig**. Kein Provider-Symbol-Mapping hier.
  **Sieben zusätzliche Instrumente (#64):** `IUSA`, `XEON`, `EXSA`, `IBCL`,
  `IBCI`, `IQQ6`, `EXXY` wurden ergänzt, um das tägliche Alpha-Vantage-Budget
  von 18 auf 25 Requests auszuschöpfen. Alle sieben sind XETRA-Symbole in
  EUR, kosten also keinen zusätzlichen FX-Request und umgehen das
  Währungsproblem aus #62 vollständig. Sie standen zunächst bewusst in
  keinem Topf (erst Daten sammeln, dann allokieren) — mittlerweile sind alle
  sieben einer Strategie zugeordnet: `IUSA` dient ausschließlich als
  Benchmark (`strategies.SP500_BENCHMARK`), die übrigen sechs sind Teil von
  `strategies.BARBELL_20_80_DIVERSIFIZIERT` (siehe unten). Ihre
  Steuerattribute (`teilfreistellung`/`thesaurierend`/`ausschuettend`) sind
  gegen öffentliche Fondsanbieter-Fact-Sheets (justETF/extraETF/onvista/DAS
  INVESTMENT) verifiziert — dabei wurde ein Fehler gefunden und korrigiert:
  `IBCI` und `EXXY` sind tatsächlich thesaurierende Acc-Anteilsklassen,
  waren aber zunächst fälschlich als ausschüttend markiert (relevant, weil
  sie jetzt tatsächlich alloziert sind und die Vorabpauschale-/
  Dividendenmodellierung dadurch beeinflusst wird).
  **Zwei Instrumente für Dividende/Value (#99):** `ISPA` (iShares STOXX
  Global Select Dividend 100, ausschüttend, Kurse ab 2009-11) und `IS3S`
  (iShares Edge MSCI World Value Factor, thesaurierend, erst ab 2014-11) —
  beide wie die #64-Instrumente XETRA/EUR, kein FX-Request. Der im Issue
  vorgeschlagene Value-Ticker `IUVL` löst bei Alpha Vantage NICHT auf; die
  XETRA-Notierung desselben Fonds läuft unter `IS3S.DEX` (`IWVL.LON` wäre USD),
  entsprechend ist auch die ISIN die der real gehandelten Acc-Anteilsklasse
  (IE00BP3QZB59) statt der im Issue vorgeschlagenen. Beide gehören
  ausschließlich zu `strategies.DIVIDENDE_UND_VALUE`.
  **Request-Budget (seit #99 je Batch):** Mit 26 Instrumenten brauchen Backfill
  *und* Wochenabruf je **27** Requests und passen damit NICHT mehr in das
  Alpha-Vantage-Tageslimit von 25. Beide laufen deshalb in zwei Batches an zwei
  aufeinanderfolgenden Tagen (`sources/alphavantage.batch_tickers()`,
  `--batch`): Batch 1 sind genau die Ticker mit Fremdwährungs-/Krypto-Endpunkt
  (alle 9 USD-Ticker + BTC-EUR, 11 Requests inkl. des einen EUR/USD-Requests),
  Batch 2 der Rest (16 EUR/XETRA-Ticker). Die Aufteilung ist bewusst
  ABGELEITET statt als zwei feste Listen hinterlegt — damit landet ein künftig
  ergänztes Instrument automatisch im richtigen Batch, und der eine
  FX-Request fällt zwangsläufig nur in einem Lauf an.
  `tests/test_backfill_history.py` hält das als Test fest, jetzt mit der neuen
  Zählweise: nicht mehr „die Gesamtzahl passt in einen Tag", sondern „JEDER
  EINZELNE Batch passt in einen Tag" (plus: die Batches decken jeden Ticker
  genau einmal ab, und nur einer braucht den FX-Request).
  **Darstellung nicht allokierter Instrumente (#66):** Weder das Dashboard
  noch die Prämissen-Seite leiten eine Instrumentenzahl mehr aus einer
  hartkodierten Konstante ab. Die README enthält seit der #64-Nachfolgearbeit
  einen `## Portfolio overview`-Abschnitt mit einer statischen Tabelle aller
  26 Ticker samt der Strategie, die sie hält — bewusst als lesbare Übersicht
  für Menschen, aber dadurch eine hartkodierte Momentaufnahme, die bei einer
  künftigen Strategie-Änderung von Hand nachgezogen werden muss (anders als
  Dashboard/Prämissen-Seite, die sich bei jedem Build automatisch aus
  `Strategy.alle_ticker_gewichte()` ableiten). Beim Ändern der
  Ticker-zu-Strategie-Zuordnung also auch diese README-Tabelle prüfen.
  `dashboard._allokierte_ticker(strategies)` leitet die
  Menge der tatsächlich einer Strategie/einem Szenario zugeordneten Ticker
  generisch aus `Strategy.alle_ticker_gewichte()` ab; `dashboard.html.j2`
  zeigt `{{ instrumente_anzahl }}` (aus `common_context`) statt einer
  hartkodierten Zahl. `_praemissen_kontext()` trennt die Instrumententabelle
  dementsprechend in `instrumente` (nur allokierte) und
  `nicht_allokierte_instrumente` — Letztere bekommen einen eigenen Abschnitt
  „Datenreihen ohne Allokation" (nur gerendert, wenn nicht leer). Seit der
  vollständigen Allokation der sieben #64-Instrumente ist diese Menge aktuell
  leer und der Abschnitt entfällt entsprechend — der Mechanismus bleibt aber
  bestehen, falls künftig wieder Instrumente ohne Topf ergänzt werden.
- `strategies.py` — austauschbare `Strategy`-Definitionen (Töpfe,
  Sub-Gewichte, Rebalancing-Schwelle, Startkapital) + strategieübergreifende
  Steuer-/Gebührkonstanten. Die Engine enthält **keine** Barbell-spezifischen
  Annahmen — neue Strategien sind nur ein weiterer Eintrag in `STRATEGIES`.
  Optionales Feld `Strategy.gewichte_fn` macht die Ziel-Gewichte zeitabhängig
  statt konstant (Signatur `(rows, i) -> dict[ticker, Decimal]`, darf nur auf
  `rows[:i+1]` zugreifen, kein Lookahead-Bias) — Basis für `scenarios.py`.
  Optionales Feld `Strategy.beitraege` (Liste von `Beitrag(name, ohne)`)
  markiert eine Strategie als *zusammengesetzt*: `ohne` ist dieselbe Strategie
  ohne genau diese eine Teilregel, das Dashboard weist deren Einzeleffekt per
  Leave-one-out aus (Rendite voll − Rendite ohne). Die `ohne`-Varianten haben
  selbst keine `beitraege`, sonst würde die Auswertung rekursiv.
  `BARBELL_20_60_20_SATELLIT` erweitert Barbell 20/80 um einen dritten Topf
  "Einzelaktien-Satellit" (10 gleichgewichtete Einzelaktien statt breiter
  ETFs): Topf A (Sicherheit) bleibt bei 20%, Topf B (breite ETFs/BTC) sinkt
  von 80% auf 60%, die freiwerdenden 20% gehen in den neuen Topf C — das
  80/20-Risikoprofil bleibt damit erhalten. Die 10 Aktien mischen bewusst
  hoch-volatile Wachstums-/Themenwerte (Lumentum, BYD, SolarEdge, SMA Solar,
  Tesla, Palantir, Strategy/vorm. MicroStrategy, Rivian) mit zwei defensiven
  Blue Chips (Coca-Cola, Roche) als Gegenbeispiel — erster Ansatz, keine
  Optimierung/Backtesting der Auswahl oder Gewichtung. Optionales Feld
  `Strategy.beschreibung` liefert die Kurzbeschreibung, die das Dashboard je
  Strategie/Szenario anzeigt (leer = keine Beschreibung). Optionales Feld
  `Strategy.optimierungen` (Instanz von `Optimierungen`, fünf Bool-Schalter
  `steueroptimierung`/`rebalancing`/`ordergebuehren`/`besteuerung`/`fondskosten`, alle
  standardmäßig `True`) bestimmt, welche der fünf strategieübergreifenden
  Simulationsmechanismen `engine.simulate()` für diese Strategie anwendet —
  `BUY_AND_HOLD` nutzt z. B. `Optimierungen(rebalancing=False)` statt einer
  künstlich unerreichbaren Rebalancing-Schwelle.
  **`BARBELL_20_80_DIVERSIFIZIERT` und `SP500_BENCHMARK` (#64):** zwei
  weitere, zusätzliche Strategien neben den bestehenden drei — sie
  verändern `BARBELL_20_80`/`BARBELL_30_70`/`BARBELL_20_60_20_SATELLIT`
  nicht, damit sich deren veröffentlichte Kennzahlen durch #64 nicht
  verschieben. `SP500_BENCHMARK` ist die einfachste mögliche Strategie: ein
  einziger Topf, 100% `IUSA`, nie rebalanciert (`Optimierungen
  (rebalancing=False)`, analog zu `BUY_AND_HOLD` — bei nur einem Instrument
  wäre der Rebalancing-Trigger ohnehin wirkungslos, aber ehrlich benannt
  statt implizit über eine unerreichbare Schwelle abgeschaltet). Dient als
  reine Vergleichslinie "einfach den Index kaufen", die dem Dashboard vorher
  fehlte. `BARBELL_20_80_DIVERSIFIZIERT` behält das 20/80-Risikoprofil von
  `BARBELL_20_80`, mischt aber die übrigen sechs #64-Instrumente ein, um
  zwei im Issue benannte Lücken zu schließen: Topf A verliert `EUNL`
  (Aktien-ETF) zugunsten von echtem EUR-Cash (`XEON`) sowie Anleihen anderer
  Duration/Realzins-Eigenschaft (`IBCL`/`IBCI`) neben `EUNA`/`4GLD`; Topf B
  bekommt `EUNL` sowie `EXSA` (Europa, senkt die USA/Tech-Konzentration von
  LYMS+SEMI), `IQQ6` (Immobilien) und `EXXY` (breite Rohstoffe). Erster
  Ansatz, Gewichte nicht optimiert/gebacktestet — wie bei allen Szenarien in
  `scenarios.py`.
  **Optionales Feld `Strategy.eigene_chart_skala` (Nachfolgearbeit zu #64):**
  rein darstellerisch, Default `False`. Die Startseite skaliert alle
  Wertverlauf-Charts standardmäßig auf ein gemeinsames Y-Achsen-Maximum
  (`dashboard.py`, `wert_chart_max`/`wertChartMax`, #24), damit Strategien
  optisch vergleichbar bleiben. `SP500_BENCHMARK` wächst über die volle
  Historie aber auf ein Vielfaches der übrigen Strategien (+918% vs. eine
  Größenordnung von +70–150%) — mit gemeinsamer Skala würden dadurch alle
  anderen Charts flachgedrückt. `eigene_chart_skala=True` nimmt eine
  Strategie aus der Berechnung des gemeinsamen Maximums heraus; ihr eigener
  Chart nutzt stattdessen `own_chart_max` (Maximum der eigenen Wertreihe,
  siehe `_build_strategy_view()`). Ändert nichts an der Simulation, nur an
  der Startseiten-Darstellung — Detailseiten sind ohnehin schon pro
  Strategie unabhängig skaliert.
  **`strategies.BENCHMARK_STRATEGIEN` und der Benchmark-Overlay-Schalter
  (#72):** Liste von Strategien (aktuell nur `SP500_BENCHMARK`), die NICHT
  in `STRATEGIES`/`SCENARIOS` stehen, sondern optional als zusätzliche Linie
  in den Wertverlauf-Charts anderer Strategien eingeblendet werden können -
  ein zentraler Schalter pro Seite (Start- **und** Detailseite, ein
  `<div class="benchmark-switch">` mit Buttons "Kein Benchmark"/je Kandidat)
  steuert dabei ALLE auf dieser Seite gerenderten Charts gleichzeitig.
  `dashboard._benchmark_reihen(rows, strategy)` simuliert jeden Kandidaten
  mit `dataclasses.replace(bench, startkapital=strategy.startkapital)` über
  exakt dieselben `rows` wie die angezeigte Strategie - dadurch hat die
  Overlay-Reihe automatisch dieselbe Länge/Reihenfolge wie deren eigener
  Wertverlauf (`engine.simulate()`: ein `ValuePoint` je Zeile in `rows`) und
  startet beim selben Kapital, ganz ohne Datums-Abgleich. Ein Kandidat wird
  nur aufgenommen, wenn ALLE seine Ticker im übergebenen Zeitraum
  mindestens einen Kurs haben (sonst bliebe die Linie bei 0) - das macht
  die Verfügbarkeit rein datengetrieben, nicht Owner-kuratiert: Fehlt ein
  Kandidat aktuell komplett in `instruments.py`/`price_history.csv` (wie
  `FR0010755611` aus #72, siehe Kommentar an `BENCHMARK_STRATEGIEN` in
  strategies.py), taucht er im Schalter einfach gar nicht erst auf, statt
  eine kaputte Option anzuzeigen. Die Strategie selbst wird als Kandidat für
  ihre eigene Seite ausgeschlossen (per Namensvergleich) - eine Linie neben
  sich selbst wäre nur redundant. Sowohl `_build_strategy_view()` (volle
  Historie, Feld `benchmarks`/`benchmarks_json`) als auch
  `_zeitraum_presets()` (je Preset ein eigenes `benchmarks`-Feld) liefern
  diese Overlay-Daten, damit der Zeitraum-Umschalter (#54) und der
  Benchmark-Schalter unabhängig voneinander funktionieren.
  **Wichtige Owner-Vorgabe: die Y-Achsen-Skalierung darf sich durch den
  Schalter nicht ändern.** Alle betroffenen Charts (Wertverlauf auf Start-
  und Detailseite, inkl. der Zeitraum-Preset-Varianten) nutzen deshalb ein
  hartes Chart.js-`max` statt `suggestedMax` - Letzteres ist nur eine
  Untergrenze und wäre von einer größeren Benchmark-Reihe überschritten
  worden, ein hartes `max` schneidet die Linie stattdessen oben am
  Chart-Rand ab. Reine Darstellungsschicht, keine neue Instrumentenzuordnung
  und kein Eingriff in `engine.py`.
  **`PORTFOLIO_60_40` und `PERMANENT_PORTFOLIO` (verbreitete klassische
  Portfolios):** zwei weitere, zusätzliche Strategien neben den bestehenden
  Barbell-Varianten und `SP500_BENCHMARK` - eigene Rubrik
  `RUBRIK_KLASSISCHE_PORTFOLIOS` statt `RUBRIK_BARBELL`/`RUBRIK_REFERENZ`,
  da beide strukturell weder Barbell-Extreme (sicherer Sockel + volatile
  Beimischung) noch eine reine "einfach den Index kaufen"-Vergleichslinie
  sind, sondern eigenständige, verbreitete Allokationsregeln. Beide nutzen
  ausschließlich bereits vorhandene Instrumente (`EUNL`/`EUNA`/`4GLD`/
  `XEON`/`IBCL`), kein 25. Instrument, keine Änderung am
  Alpha-Vantage-Request-Budget. `PORTFOLIO_60_40` ist das meistzitierte
  Referenzportfolio überhaupt: zwei Töpfe, 60% `EUNL` (breiter
  Aktienmarkt), 40% `EUNA` (breite Anleihen) - eine einzige glatte
  Aufteilung zwischen zwei Anlageklassen statt eines Extrem-Ansatzes.
  `PERMANENT_PORTFOLIO` (Harry Browne) hält stattdessen vier gleich große
  Töpfe zu je 25%: `EUNL` (Aktien), `IBCL` (lange Anleihen), `4GLD`
  (Gold), `XEON` (Cash) - konzipiert, um in jedem der vier
  Wirtschaftsklimata (Wachstum, Rezession, Inflation, Deflation)
  mindestens einen gut laufenden Baustein zu halten, und damit inhaltlich
  der interessanteste Kontrast zur Barbell-Idee. Beide setzen wie alle
  neueren Strategien explizit `rebalancing_schwelle_pp=5` und
  `rebalancing_schwelle_relativ=Decimal("0.25")` (die 5/25-Regel, siehe
  unten) und verwenden je Topf genau ein Instrument mit `sub_gewichte={
  ticker: Decimal("1")}` - anders als die Barbell-Strategien, deren Töpfe
  mehrere Instrumente mischen. Erster Ansatz wie alle Strategien/Szenarien:
  die 60/40- und 25/25/25/25-Gewichte sind die literarisch bzw. historisch
  überlieferten Werte, nicht für dieses Instrumentenset optimiert oder
  gebacktestet.
  **Rebalancing-Trigger, "5/25-Regel je Topf" (#63, F5):** Optionales Feld
  `Strategy.rebalancing_schwelle_relativ` (Decimal, Default `1` = 100%)
  ergänzt `rebalancing_schwelle_pp` um eine relative Zusatzschwelle. Die
  Engine rebalanciert, sobald IRGENDEIN Topf entweder um mehr als
  `rebalancing_schwelle_pp` Prozentpunkte ABSOLUT vom eigenen (per
  `handelbare_gewichte()` umgelegten) Zielgewicht abweicht, oder um mehr als
  `rebalancing_schwelle_relativ` RELATIV zu diesem Zielgewicht — je nachdem,
  welche der beiden Schwellen zuerst greift (Marktstandard-"5/25-Regel").
  Vorher prüfte der Trigger ausschließlich `ziel_topf` (Topf A) mit einer
  rein absoluten Schwelle; bei genau zwei komplementären Töpfen (A+B=100%)
  ist die Abweichung von A immer exakt die von B, weshalb diese Änderung für
  zweitöpfige Strategien/Szenarien allein wirkungslos bliebe — bei drei oder
  mehr Töpfen (`BARBELL_20_60_20_SATELLIT`) kann seither aber ein einzelner
  Topf unbemerkt driften, während Topf A zufällig im Band bleibt. Der
  Default `rebalancing_schwelle_relativ=1` macht die relative Schwelle
  faktisch wirkungslos (ein Zielgewicht kann nie um mehr als 100% seiner
  selbst abweichen) — Strategien/Tests ohne explizit gesetzten Wert verhalten
  sich deshalb exakt wie vor #63. Alle drei Produktivstrategien sowie alle
  Szenarien in `scenarios.py` setzen explizit `rebalancing_schwelle_pp=5` und
  `rebalancing_schwelle_relativ=Decimal("0.25")` (die kanonische 5/25-Regel,
  Owner-Entscheidung zu #63) und ersetzen damit die vorherigen, uneinheitlich
  gewählten Werte (10/15/3 Prozentpunkte je Strategie/Szenario).
- `scenarios.py` — Auswertungs-Szenarien als gewöhnliche `Strategy`-Instanzen
  mit gesetztem `gewichte_fn`, in drei Kategorien: (1) Börsenweisheiten — seit
  #30 bewusst fünf der bekanntesten ENGLISCHEN Börsenweisheiten statt
  deutscher Sprüche/Übersetzungen als Grundlage, jeweils einzeln als eigenes
  Szenario: "Sell in May and Go Away" (saisonal defensiv Mai–September), "Time
  in the Market Beats Timing the Market" (nie aktiv rebalancieren — interner
  Python-Bezeichner/Slug bleibt `BUY_AND_HOLD`, weil "Buy & Hold" selbst ein
  etablierter englischer Fachbegriff ist), "Santa Claus Rally" (Dez/Jan
  Wachstumsquote auf 95%), "Buy the Dip" (Wachstumsquote auf 95% nach >10%
  Rückgang vom Rolling-Hoch) und "Cut Your Losses" (Trailing-Stop je
  Wachstums-Instrument, >15% Rückgang vom eigenen Rolling-Hoch schaltet nur
  dieses Instrument auf 0%) sowie "Börsenweisheiten (alle fünf kombiniert)",
  das die fünf zu einer Strategie zusammenfasst: jede Weisheit ist ein
  `Weisheit`-Baustein, der in Phase 1 eine Wachstumsquote votiert (oder sich
  enthält, wenn seine Bedingung diese Woche nicht greift) und/oder in Phase 2
  als Instrument-Overlay wirkt. Ziel-Quote ist das **arithmetische Mittel der
  abgegebenen Voten** — widersprüchliche Signale (Mai-Ausstieg vs. Dip-Kauf)
  heben sich damit teilweise auf, statt dass eine Regel die anderen
  überstimmt; "Time in the market beats timing the market" votiert als
  einzige immer (für die normale Quote) und wirkt so als dämpfender Anker —
  dieser Mechanismus-Unterschied zum Solo-Szenario (dort schaltet
  `BUY_AND_HOLD` stattdessen komplett das Rebalancing ab) steht seit #27 auch
  als `beschreibung` auf der `BUY_AND_HOLD`-Detailseite, nachdem der Owner
  entschieden hat, dass die Mechanik selbst unverändert bleibt. "Cut your
  losses short, let your winners run" ist die einzige Overlay-Regel und läuft
  nach Phase 1. Der Einzeleffekt jedes Spruchs kommt über `Strategy.beitraege`
  (Leave-one-out) ins Dashboard. Die fünf einzelnen Weisheiten-Szenarien
  tragen zusätzlich `Strategy.teil_von = BOERSENWEISHEITEN_NAME` (#30) - rein
  deklarativ, ändert nichts an der Simulation, macht sie aber im Dashboard als
  Unterszenarien der Kombi-Strategie erkennbar: `dashboard.
  _teilszenario_gruppen()` gruppiert sie serverseitig für einen gemeinsamen
  Vergleichs-Chart auf der Startseite (Kombi-Strategie + alle Unterszenarien
  im selben Chart, statt verstreut zwischen allen übrigen Strategien/
  Szenarien) - generisch über `teil_von`, nicht auf die Börsenweisheiten fest
  verdrahtet, falls künftig weitere zusammengesetzte Strategien Unterszenarien
  bekommen; (2) Charttechnik — SMA-Crossover (Golden/Death Cross,
  10/40 Wochen) auf dem MSCI-World-ETF, seit #28 zusätzlich als eigenes
  Szenario `CHART_SMA_CROSSOVER_KURZ` mit verkürztem Zeitraum (4/20 Wochen ≈
  21/100 Handelstage statt 50/200, gleiche 5-Handelstage/Woche-Näherung wie
  beim Original) für ein reaktionsschnelleres Signal; (3) weitere Ansätze — Momentum-/
  Relative-Stärke-Rotation (Top-2 der Wachstums-Instrumente nach
  12-Wochen-Trailing-Rendite), volatilitätsbasierte Aktienquote (50–90%
  Wachstum je nach realisierter EUNL-Volatilität) und Cost-Average-Einstieg
  (Wachstumsquote rampt über 10 Wochen linear hoch statt Einmalanlage).
  Erster Ansatz, Parameter nicht optimiert/gebacktestet.
  `scripts/build_dashboard.py` rendert `STRATEGIES + SCENARIOS` standardmäßig.
- `history_store.py` — **einziger** Schreibzugriff auf
  `data/price_history.csv` / `data/fetch_log.csv`. `record_week()` ist
  Wochen-idempotent (schlüsselt über ISO-Kalenderwoche, nicht Kalenderdatum)
  und macht Carry-Forward bei fehlenden Kursen (nie eine Zeile mit Lücke,
  sofern ein Vorwert existiert) — dabei ausschließlich aus Wochen **vor**
  der Zielwoche, damit ein nachträglich gefüllter Eintrag keinen Kurs aus
  der Zukunft übernimmt. `row_date_from_quotes()` bestimmt das Zeilendatum
  aus dem von der Quelle gemeldeten **Handelstag** (häufigster Handelstag
  der erfolgreichen Quotes, bei Gleichstand der frühere) statt aus dem
  Abrufdatum: ein Montagslauf vor Börsenbeginn liefert den Freitagsschluss
  der Vorwoche, der sonst eine ISO-Woche zu spät und damit versetzt zum
  Backfill einsortiert würde.
  **Teilabrufe derselben ISO-Woche sind additiv (#99):** Seit der Wochenabruf
  in zwei Batches an zwei Tagen läuft, existiert beim zweiten Lauf bereits eine
  Zeile für die Zielwoche. Für einen Ticker ohne frischen Kurs gilt deshalb
  ZUERST der bereits in dieser Zeile stehende Wert und erst danach der
  Carry-Forward aus früheren Wochen — sonst würde der zweite Batch die am
  Vortag frisch geholten Kurse des ersten auf den Stand der Vorwoche
  zurücksetzen (`last_known` ist bewusst nur aus Wochen VOR der Zielwoche
  gespeist). Nur ein Ticker, der auch in der bestehenden Wochenzeile fehlt
  (Sicherheitsnetz), fällt weiterhin auf den alten Carry-Forward zurück. Der
  neue Parameter `angefragte_ticker` benennt die Ticker, für die dieser Lauf
  zuständig war (Default: alle): nur für sie wird ein fehlender Kurs in
  `fetch_log.csv` protokolliert — ein Ticker aus dem anderen Batch ist nicht
  „eingefroren", und ein Log-Eintrag dafür würde im Dashboard (#42) eine
  Kurslücke melden, die es nicht gibt. Das Zeilendatum ist bei einer
  bestehenden Wochenzeile das FRÜHERE der beiden Daten (vorher gewann das
  spätere), damit die Historie nicht davon abhängt, welcher der beiden Läufe
  zuletzt durchlief. `read_fetch_log()` liest `fetch_log.csv`
  zurück (`FetchLogEntry(date, ticker, status, source, note)`) – bislang nur
  geschrieben, seit #42 auch gelesen, um im Dashboard sichtbar zu machen,
  welche Kurse zuletzt fortgeschrieben statt frisch abgerufen wurden.
- `sources/` — austauschbare `PriceSource`-Implementierungen
  (`sources/__init__.py` definiert das Protokoll). **Standard:**
  `alphavantage.py` (offizielle REST-API, braucht `ALPHAVANTAGE_API_KEY`).
  `yfinance_stooq.py` existiert noch als Referenzimplementierung, wird aber
  von `scripts/run_fetch.py` nicht mehr aufgerufen (yfinance scheiterte
  produktiv wiederholt an Yahoos Crumb/Cookie-Auth). Provider-Symbol-Mapping
  gehört ausschließlich in die jeweilige Source-Datei — jeder Ticker in
  `instruments.py` (außer BTC-EUR, eigener Krypto-Endpunkt) braucht einen
  Eintrag in `ALPHAVANTAGE_SYMBOLS`, sonst liefert der Abruf stillschweigend
  "missing" (siehe `tests/test_satellit_strategy.py` für den
  Regressionstest, der das für alle Ticker prüft). **Währungskonsistenz:**
  `USD_TICKERS` markiert die Satelliten-Aktien ohne EUR-Notierung (alle
  außer S92/SMA Solar) - `AlphaVantageSource.fetch()` rechnet sie bei jedem
  Abruf per aktuellem `CURRENCY_EXCHANGE_RATE` (USD→EUR) um, damit die
  (währungsblinde) Engine nicht USD-Beträge als EUR fehlinterpretiert.
  Schlägt der EUR/USD-Abruf fehl, werden betroffene Ticker als `missing`
  markiert statt falsch umgerechnet zu werden.
- `engine.py` — die Simulation. Modellierungsentscheidungen, die beim
  Ändern zu beachten sind: Initialkauf-Gebühren werden vom Startkapital
  *vor* der Aufteilung abgezogen; spätere Trades (Rebalancing,
  Dezember-Harvest) mindern beim Verkauf den realisierten Gewinn um die
  Gebühr und schlagen sie beim Kauf auf die Kostenbasis; Kostenbasis läuft
  nach der Durchschnittskosten-Methode (kein FIFO/LIFO); Rebalancing bringt
  bei Auslösung *alle* Instrumente auf ihr Zielgewicht zurück, nicht nur den
  auslösenden Topf; alle Geld-/Stückzahl-Arithmetik nutzt `Decimal`, nie
  `float`. **Rebalancing-Trigger (#63, F5):** prüft seit #63 JEDEN Topf der
  Strategie (nicht mehr nur `strategy.ziel_topf`) gegen die "5/25-Regel" aus
  `Strategy.rebalancing_schwelle_pp`/`rebalancing_schwelle_relativ` (siehe
  `strategies.py`) — sobald ein Topf entweder die absolute oder die relative
  Schwelle überschreitet, läuft ein vollständiges `rebalance_to_targets()`
  auf alle Instrumente, wie zuvor. Die Filterung nach look-ahead-verzerrten
  Frühphasen (F4, siehe `dashboard.py`) passiert bewusst NICHT hier, sondern
  ausschließlich in der Darstellungsschicht, bevor `rows` an `simulate()`
  übergeben werden — `engine.py` bleibt dadurch unverändert gegenüber den
  bestehenden, gegen die volle Testhistorie hand-gerechneten Engine-Tests.
  **Werterhaltung beim Rebalancing (wichtig beim Ändern):**
  `rebalance_to_targets()` führt kein Cash-Konto — Verkaufserlöse werden
  nirgends gutgeschrieben, Kaufbeträge nirgends entnommen. Die Umschichtung
  ist allein dadurch summenneutral, dass sich die `diffs` über *alle*
  Instrumente zu genau dem vorhandenen `pending_cash` aufaddieren. Ein
  Instrument einfach zu überspringen zerstört diese Invariante und lässt
  Geld ersatzlos verschwinden. Für Instrumente ohne Kurs in der aktuellen
  Zeile wird der Zielanteil deshalb als `pending_cash` geparkt — dieselbe
  Mechanik wie beim Initialkauf (`delayed_initial_buy`). Vor dieser
  Korrektur schrumpften alle rebalancierenden Strategien über die lange
  Historie wöchentlich um ~50% bis auf 0 EUR (nur `BUY_AND_HOLD` blieb
  korrekt, da es nie rebalanciert) — Regressionstests in
  `tests/test_engine.py`.
  **Noch nicht existierende Instrumente (`handelbare_gewichte()`):** Über
  die 20-Jahres-Historie existiert ein großer Teil der Instrumente anfangs
  noch nicht (Bitcoin vor 2009, Rivian vor dem IPO 2021, die meisten ETFs
  am Anfang). Ihr Zielanteil wird **anteilig auf die tatsächlich
  handelbaren Instrumente umgelegt**, statt ihn unverzinst zu parken —
  sonst lägen zu Beginn der Historie über 60% des Depots brach und die
  Rendite der frühen Jahre wäre praktisch aussagelos (gemessen: Endwert
  89.408 € beim Parken gegenüber 125.893 € beim Umlegen). Die relativen
  Verhältnisse *innerhalb* der verfügbaren Instrumente bleiben dabei
  erhalten; das Depot bleibt voll investiert, in dem, was es zu diesem
  Zeitpunkt gab. Zwei Ereignisse setzen Kapital neu an, **beide bewusst
  unabhängig von `opt.rebalancing`** (es sind Erstkäufe, kein Korrigieren
  von Drift — dieselbe Logik wie beim bisherigen `delayed_initial_buy`,
  sonst hielte `BUY_AND_HOLD` nie ein Instrument, das es bei
  Simulationsbeginn noch nicht gab): `neues_instrument`, sobald ein Ticker
  erstmals einen Kurs hat, und `kapitaleinsatz`, sobald geparktes Cash
  wieder ein handelbares Ziel hat. **Aber nur der Erstkauf selbst ist von
  `opt.rebalancing` unabhängig, nicht der übrige Bestand (#62):** bei
  ausgeschaltetem Rebalancing baut `erstkauf_gewichte()` eine Zielverteilung,
  in der das neue Instrument sein reguläres Zielgewicht bekommt und der
  gesamte Rest die *aktuellen* Marktwert-Verhältnisse behält (nur proportional
  herunterskaliert). Vorher lief bei jedem Börsengang ein komplettes
  Rebalancing über `current_weights` — über die 20-Jahres-Historie 27
  verdeckte Voll-Rebalancings, wodurch „Time in the market beats timing the
  market" bit-identische 1-/3-/5-Jahres-Renditen wie die rebalancierende
  Barbell-Strategie lieferte und am Ende exakt deren Zielgewichte hielt. Letzteres ist nötig, weil der
  Rebalancing-Trigger nur Topf A prüft: war zeitweise *kein* Zielinstrument
  handelbar (z. B. „Sell in May" startet im September 2006 defensiv, Topf A
  existiert aber erst ab 2008), sind Ist- und Zielgewicht von Topf A beide
  0 und das geparkte Kapital käme nie wieder zum Einsatz. Bleibt bei
  `summe <= 0` (kein einziges Zielinstrument handelbar) alles geparkt, ist
  das gewollt: „raus aus dem Markt" ohne verfügbares defensives Instrument
  *ist* Cash. **Warum das kaum vorkommt:** Die Strategien haben ohnehin
  keine eigene Cash-Position im Zielportfolio (siehe Abschnitt „Cash und
  ungenutztes Kapital" auf `docs/praemissen.html`, #35) — Topf A übernimmt
  die Cash-Rolle. Geparktes Kapital
  ist deshalb ein rein technischer, vorübergehender Zustand
  (`pending_cash`), keine gewollte Anlageklasse. Gegen die reale
  20-Jahres-Historie geprüft (Stand #55): über alle Strategien und
  Szenarien hinweg liegt zu **jedem** Zeitpunkt 0% Cash, mit einer einzigen
  Ausnahme — „Sell in May" hält 27 Wochen lang 100% Cash (September 2006
  sowie Mai–September 2007), weil 4GLD als einziges Topf-A-Instrument
  dieser Frühphase erst ab 2008-01-11 einen Kurs hat und die defensive
  Season damit buchstäblich kein Ziel hatte. Ab 2008 tritt der Fall nie
  wieder auf. Dezember-Harvest realisiert Verluste (größter zuerst) bis der
  verbleibende Sparerpauschbetrag des Jahres gedeckt ist, mit sofortigem
  Rückkauf zum selben Kurs. `simulate(price_history, strategy,
  optimierungen=None)` nimmt optional eine `Optimierungen`-Instanz entgegen
  (Default: `strategy.optimierungen`) und schaltet damit die fünf
  strategieübergreifenden Mechanismen einzeln ab: `ordergebuehren=False`
  macht alle Trades gebührenfrei (lokale `gebuehr`-Variable statt der
  Konstante `ORDERGEBUEHR`), `besteuerung=False` lässt `process_realized_gain`
  früh zurückkehren (Freibetrag/Verlustvortrag/kumulierte Steuer bleiben
  unverändert — der simulierte Portfoliowert selbst wird nirgends um Steuer
  gemindert, das ist reines Tracking), `rebalancing=False` überspringt die
  periodische Rückführung auf die Zielgewichte, `steueroptimierung=False`
  überspringt den kompletten Dezember-Harvest-Block, `fondskosten=False`
  lässt die laufenden Fondskosten (`Instrument.ter`, #76) weg. Diese Schalter
  dienen dazu, den isolierten Renditebeitrag jedes Mechanismus messbar zu
  machen (#17) — siehe `dashboard._optimierungs_effekte()`.
  **Steuerkorrekturen (#37/#38/#39, Paket A aus #46)**, alle über
  `Instrument`-Felder in `instruments.py` gesteuert, nicht über
  `Optimierungen` (das sind Modellfehler-Korrekturen, keine ein-/
  ausschaltbaren Mechanismen):
  - `Instrument.teilfreistellung` (#38): `process_realized_gain(gain,
    ticker)` multipliziert Gewinn *und* Verlust vor der Verrechnung mit
    `(1 - teilfreistellung)` — 30% für die vier Aktienfonds-ETFs (EUNL,
    LYMS, SEMI, EIMI), 0% für Rentenfonds (EUNA), physisches Gold (4GLD),
    Einzelaktien und BTC-EUR (kein Fondsprivileg).
  - `Instrument.thesaurierend` (#39): `apply_vorabpauschale()` läuft bei
    jeder Harvest-Zeile (`harvest_dates`, unabhängig von
    `opt.steueroptimierung`, nur an `opt.besteuerung` gekoppelt) *vor* der
    A/B-Entscheidung und wendet je thesaurierendem Instrument
    `min(Wert_Jahresbeginn × VORABPAUSCHALE_BASISZINS_PLATZHALTER ×
    VORABPAUSCHALE_FAKTOR, tatsächliche Wertsteigerung)` als zusätzlichen
    Gewinn auf denselben Freibetrag-Topf an (inkl. Teilfreistellung) und
    hebt die Kostenbasis um den vollen, unversteuerten Vorabpauschale-Betrag
    an. `wert_jahresbeginn` wird am Ende jeder Zeile für das jeweils neue
    Jahr aus den aktuellen `values` mitgeschrieben. **Bewusster Platzhalter:**
    `VORABPAUSCHALE_BASISZINS_PLATZHALTER` in `strategies.py` ist ein
    konstanter Ersatzwert, kein echter jährlicher BMF-Basiszins (siehe
    TODO-Kommentar dort) — die Anwendung am Jahresende statt am 1. Werktag
    des Folgejahres ist ebenfalls eine bewusste Vereinfachung der exakten
    gesetzlichen Fälligkeit.
  - `Instrument.spekulationsfrist_tage` (#37, nur BTC-EUR = 365): jede
    `_Position` führt zusätzlich `kauf_tage_gewichtet` (stückzahlgewichtete
    Summe der Kauf-Ordinaldaten, exakt analog zu `cost_total`/`avg_cost()`)
    für ein vereinfachtes gewichtetes Kaufdatum
    (`avg_kauf_tag_ordinal()`) statt echtem Per-Lot-Tracking. Bei jedem
    Verkauf entscheidet `process_gain_for_sale()` anhand der Haltedauer
    (Verkaufsdatum minus `avg_kauf_tag_ordinal()` vor dem Verkauf): über der
    Frist steuerfrei (§ 23 EStG, berührt keinen Topf), sonst
    `process_spekulationsgeschaeft()` — ein von Sparerpauschbetrag/
    Verlustvortrag komplett getrennter Topf mit eigenem Verlustvortrag und
    einer *Freigrenze* (§ 23 Abs. 3 Satz 5 EStG, `SPEKULATIONSFRIST_
    FREIGRENZE_PRO_JAHR`) statt eines Freibetrags: unterhalb der Grenze
    bleibt der GESAMTE Jahresgewinn steuerfrei, oberhalb wird der GESAMTE
    Jahresgewinn steuerpflichtig (Kippgrenze, nicht Sockelbetrag) — als
    Differenz `steuer(neuer Stand) - steuer(alter Stand)` pro Trade
    berechnet, damit der rückwirkende Kipp-Effekt trotz zeilenweiser
    Verarbeitung korrekt entsteht. `december_gewinnmitnahme()`/
    `december_tax_loss_harvest()` schließen Instrumente mit gesetzter
    Spekulationsfrist explizit aus ihren Kandidatenlisten aus, da beide
    Maßnahmen ausschließlich den Abgeltungsteuer-Topf optimieren.
    Vereinfachung: Besteuerung mit dem pauschalen `STEUERSATZ` statt dem
    tatsächlich anzuwendenden persönlichen Einkommensteuersatz.
  **Ausschüttungsrendite je Instrument (#74):** `Instrument.dividendenrendite`
  (optional, `None` → Rückfall auf `DIVIDENDENRENDITE_PLATZHALTER`) löst den
  einen Pauschalsatz für alle ausschüttenden Instrumente ab. Sieben
  Satelliten-Aktien (TSLA, RIVN, PLTR, MSTR, SEDG, LITE, S92) stehen jetzt auf
  `ausschuettend=False`, weil sie real keine Dividende zahlen — vorher bekamen
  sie jährlich 2,5% geschenkt; umgekehrt waren die Anleihen-/Immobilien-ETFs
  und `IUSA` (die Benchmark-Vergleichslinie) zu niedrig angesetzt. Beim
  Ergänzen eines ausschüttenden Instruments also einen belegten Satz setzen,
  statt den Platzhalter greifen zu lassen.
  **Laufende Fondskosten (#76):** `Instrument.ter` (Default `0`), in
  `engine.simulate()` wöchentlich pro rata (`ter/52`) als Reduktion der
  **Stückzahl** abgezogen, nicht als Barabfluss — die Kostenbasis bleibt
  bewusst unverändert (die TER ist keine steuerlich abzugsfähige Position).
  Steuerbar über den fünften `Optimierungen`-Schalter `fondskosten`. Bestehende
  handgerechnete Engine-Tests, die ETFs verwenden, setzen `fondskosten=False`,
  damit sie weiterhin genau ihren eigenen Mechanismus prüfen.
  **Liquidationswert nach Steuer (Sofortverkauf zum Stichtag):** `kumulierte_
  steuer` (siehe oben) erfasst nur bereits TATSÄCHLICH realisierte Trades
  (Rebalancing, Dezember-Harvest) — der Anteil des Endwerts, der noch in
  unrealisierten Gewinnen laufender Positionen steckt, bleibt dort unversteuert.
  `SimulationResult` führt deshalb drei zusätzliche Felder
  (`liquidationswert_nach_steuer`/`liquidationssteuer`/`liquidationsgebuehren`):
  ein hypothetischer Verkauf des GESAMTEN Depots zum Stichtag der letzten
  Kurszeile, berechnet direkt im Anschluss an die Hauptschleife in
  `simulate()` (braucht Zugriff auf `positions[t].cost_total`/
  `avg_kauf_tag_ordinal()`, die nicht Teil von `SimulationResult` sind — daher
  kein Darstellungsschicht-Mechanismus wie sonst üblich). Spiegelt denselben
  Verkaufsmechanismus wie `rebalance_to_targets()`: Ordergebühr mindert den
  realisierten Gewinn (`gain_roh = wert - cost_total - gebuehr`), Teilfreistellung
  gilt symmetrisch für Gewinn/Verlust, und Instrumente mit Spekulationsfrist
  (#37, BTC-EUR) laufen über die getrennte Freigrenze statt den
  Sparerpauschbetrag/Verlustvortrag zu berühren. Rechnet dabei bewusst auf
  KOPIEN von `freibetrag_verbleibend`/`verlustvortrag`/`spek_verlustvortrag`/
  `spek_gewinn_jahr` statt die nonlocal-Variablen zu mutieren — `tax_status`
  bleibt dadurch unverändert eine Aussage über tatsächlich realisierte Gewinne,
  die neuen Felder sind eine rein additive Momentaufnahme obendrauf.
  `dashboard._build_strategy_view()` zeigt das Ergebnis auf der Detailseite in
  der Box „Wert nach Steuern beim sofortigen Verkauf" — bewusst zusätzlich zur
  „Geschätzten Nettorendite" (F6a) statt als deren Ersatz: F6a zieht nur die
  bereits realisierte Steuer ab, diese Box zusätzlich die auf die noch
  unrealisierten Gewinne UND die Verkaufsgebühren, ist also die realistischere
  Antwort auf „was bleibt vom eingesetzten Kapital tatsächlich übrig".
- `dashboard.py` + `templates/` — reine Darstellungsschicht, rendert
  `engine.simulate()`-Ergebnisse für alle (oder eine ausgewählte)
  Strategie(n) aus `STRATEGIES`. Seit #31 mehrere Seitentypen statt einer
  einzigen `index.html`: `templates/base.html.j2` definiert Kopf/Fuß/Styles
  einmal per Jinja-Vererbung (`{% extends %}` + Blocks `title`/
  `header_extra`/`content`/`scripts`) und enthält das Drei-Punkt-Menü
  (`<details class="menu">`, reines CSS/HTML ohne JS), über das von **jeder**
  Seite die Übersicht, die Vergleichs-, die Portfolio- und die Prämissen-Seite
  erreichbar sind;
  `templates/dashboard.html.j2` (die
  Startseite `docs/index.html`) beginnt seit #88 mit einer Einleitung
  ("Worum es hier geht": warum überhaupt, die Grundidee der mindestens zwei
  Töpfe A/B, Verweis auf die Prämissen im Drei-Punkt-Menü) und zeigt vom
  strategieübergreifenden Vergleich nur noch das CAGR-**Balkendiagramm** samt
  Link auf `vergleich.html`;
  darunter (#30) je Gruppe zusammengesetzter Strategien mit Unterszenarien
  (aktuell: die Börsenweisheiten) einen eigenen "<Kombi-Name> im Vergleich"-
  Abschnitt mit einem Mehrfach-Linienchart aus `_teilszenario_gruppen()`
  (Kombi-Strategie + alle ihre `teil_von`-Unterszenarien im selben Chart,
  gemeinsame Y-Achsen-Skalierung nur innerhalb der Gruppe), sowie je Strategie
  nur Name, Kurzbeschreibung und den Wertverlauf-Chart (mit gemeinsamer
  Y-Achsen-Skalierung über alle Strategien hinweg, siehe unten);
  **Verlängerter Auswertezeitraum (#91):** Ein Schalter im Drei-Punkt-Menü
  (`data-erweitert-btn`) stellt ALLE angezeigten Zahlen auf einen gemeinsamen,
  deutlich längeren Zeitraum um — die Historie ab dem ersten Kurs des
  Ersatzbonds (`_erweiterte_rows()`, aktuell `IBCL` ab 2007-05-18) mit der
  Ersatzbond-Annahme aus #80 statt des je Strategie zugeschnittenen
  F4-Zeitraums (#63). Gerechnet wird beim Build:
  `_erweiterte_kennzahlen(strategy, rows)` liefert je Strategie ein zweites,
  bewusst schlankeres Bündel Anzeige-Labels (keine Leave-one-out-Effekte, keine
  Walk-Forward-Segmente, keine Presets — nur was der Schalter tatsächlich
  austauscht), im View unter `view["erweitert"]`. Im Browser wird nur
  umgeschaltet: Textknoten über `data-erweitert` (bzw.
  `data-i18n-en-erweitert` für Englisch), Charts über in
  `window.__erweitertRefresh` registrierte Funktionen — die Wertverlauf-Charts
  wählen dabei den ohnehin vorhandenen „Erweitert"-Preset (#80), der
  Gruppen-Chart der Börsenweisheiten (der keinen Preset-Umschalter hat) bekommt
  seine Reihen aus `gruppe.erweitert_json`. Tabelle und Balkendiagramm werden
  dabei neu sortiert, weil die Reihenfolge des Standard-Zeitraums nicht mehr
  passt. **Bewusst nicht persistiert** (Owner-Vorgabe): der Zustand lebt nur in
  einer JS-Variablen, nach dem Neuladen ist der Schalter wieder aus — anders
  als die Sprachwahl, die im `localStorage` steht. `_erweiterte_kennzahlen()`
  läuft für JEDE Strategie (auch wo keine Historie dazukommt), damit im
  eingeschalteten Zustand wirklich jede Tabellenzeile denselben Zeitraum
  abdeckt; der Schalter selbst erscheint nur (`erweitert_verfuegbar`), wenn
  mindestens eine Strategie dadurch länger wird. Die Prämissen-Seite erklärt
  die Annahme unter `id="erweiterter-zeitraum"`, das Menü verlinkt direkt
  dorthin.
  **Sprach- und Zeitraum-Umschaltung teilen sich eine Textfunktion**
  (`applyTexts()` in `base.html.j2`): beide ersetzen den Text derselben
  Elemente, getrennte Mechanismen würden sich gegenseitig überschreiben. Je
  Element gilt: Grundtext = Deutsch/eigener Zeitraum, `data-i18n-en` =
  Englisch, `data-erweitert` = Deutsch/verlängert,
  `data-i18n-en-erweitert` = beides. Elemente mit einem `.info-tip`-Kind
  bekommen nur ihren eigenen Textknoten ersetzt statt `textContent` — vorher
  verschwand das Info-Icon (#81) beim ersten Sprachwechsel.
  **Zwei eigene Seiten statt Startseiten-Abschnitten (#88):**
  `templates/vergleich.html.j2` (`docs/vergleich.html`) trägt die nach CAGR
  sortierte Übersichtstabelle (Zeilen verlinken auf die Detailseiten), die
  Korrelationsgrafik CAGR-gegen-Max-Drawdown (#82) und - bewusst UNTER den
  Daten statt davor, damit ein Erstbesucher nicht erst vier Absätze lesen muss -
  den Abschnitt "Wie die Zahlen zu lesen sind" (Vergleichszeitraum,
  Überrendite, Kennzahl-Definitionen). `templates/portfolio.html.j2`
  (`docs/portfolio.html`) trägt die Topf-Erklärung (A/B/C) und die Tabelle aller
  wöchentlich abgerufenen Instrumente (#79). Beide Seiten leiten sich
  vollständig aus denselben `views`/`common_context` ab wie die Startseite -
  `build_dashboard()` simuliert dafür nichts zusätzlich. Die Vergleichsseite
  bringt ihren eigenen kleinen Chart.js-Vorspann mit (`colors`/`commonScales`/
  `T()`/`chartRefreshers`), weil `const`-Deklarationen im globalen Skript-Scope
  seitenweit eindeutig sein müssen - dieselbe Duplizierung wie in
  `strategy_detail.html.j2`.
  `templates/strategy_detail.html.j2` rendert für **jede** Strategie/jedes
  Szenario eine eigene `docs/<slug>.html` mit allem anderen (Kennzahl-
  Kacheln, Steuer-Stats, Topf-Gewichtung Ist/Ziel, Instrumententabelle) plus
  einem zusätzlichen Chart: Wertverlauf mit 10-/40-Wochen gleitendem
  Durchschnitt als Wochen-Näherung der klassischen 50-/200-Tage-Linien
  (`dashboard._moving_average()`, 5 Handelstage/Woche — derselbe Ansatz wie
  `scenarios.CHART_SMA_CROSSOVER`, hier aber rein zur Anzeige, keine
  Handelsregel). `build_dashboard()` gibt weiterhin nur den Pfad zu
  `index.html` zurück, schreibt die `<slug>.html`-Dateien aber als
  Seiteneffekt daneben. Rendite und URL-Slug je Strategie werden rein aus
  den vorhandenen `engine.simulate()`-Ergebnissen abgeleitet. Strategien mit
  gesetztem `beitraege` bekommen auf ihrer Detailseite zusätzlich einen
  Abschnitt "Effekt der einzelnen Börsenweisheiten" (Balkendiagramm +
  Tabelle): je Teilregel die Leave-one-out-Differenz in Prozentpunkten.
  Jede Detailseite bekommt außerdem unbedingt den Abschnitt "Effekt der
  Optimierungs-Schalter" (`dashboard._optimierungs_effekte()`): je einer der
  fünf `Optimierungen`-Mechanismen (#17) als Leave-one-out-Differenz zur
  Rendite mit genau diesem Mechanismus aus. Für beide Abschnitte simuliert
  die Darstellungsschicht die Vergleichsvarianten zusätzlich — auch das
  bleibt reine Anwendung von `engine.simulate()`, keine eigene
  Berechnungslogik. Drei weitere reine Anzeige-Ableitungen (#40/#41/#42),
  alle ohne Rückwirkung auf Simulation oder Renditezahlen:
  `_volatilitaet_pct()`/`_max_drawdown_pct()` ergänzen die Übersichtstabelle
  um annualisierte Volatilität (Standardabweichung der Wochenrenditen ×
  √52) und Max Drawdown je Strategie/Szenario, damit eine hohe Rendite
  konzentrierter oder ungetesteter Regeln (z. B. Chartsignale) nicht ohne
  Risikobezug als überlegen erscheint; jede Zeile der Instrumententabelle
  bekommt zusätzlich `abweichung_pp_label`/`konzentration_warnung` (⚠, wenn
  ein einzelnes Instrument stärker als die Rebalancing-Schwelle der
  Strategie vom Zielgewicht abweicht — der Topf-A-Rebalancing-Trigger prüft
  nur die Topf-Ebene, nicht die Konzentration innerhalb eines Topfs) sowie
  `carry_forward_wochen` aus `_carry_forward_streaks()` (⚠ seit N Wochen
  eingefroren, aus `fetch_log.csv` via `read_fetch_log()`).
  `build_dashboard()` nimmt dafür optional `fetch_log` entgegen;
  `scripts/build_dashboard.py` übergibt `read_fetch_log()` standardmäßig.
  Zwei weitere reine Anzeige-Ableitungen zur Fundiertheit von Invest-
  Entscheidungen: `_sharpe_ratio()`/`_sortino_ratio()` ergänzen die
  Übersichtstabelle um risikoadjustierte Rendite (annualisierte
  Überrendite ÷ annualisierte Volatilität bzw. ÷ Downside-Deviation nur der
  Verlustwochen, `_downside_deviation()`) — eine hohe Rendite bei ebenso
  hoher Streuung ist kein besseres Ergebnis als eine niedrigere Rendite bei
  wenig Risiko. Der risikofreie Zins dafür kommt seit #75 aus dem
  Geldmarkt-ETF statt aus einer Konstante (siehe „Risikofreier Zins" weiter
  unten); `_RISIKOFREIER_ZINS_PLATZHALTER` ist nur noch Rückfallwert. Jede
  Detailseite bekommt zusätzlich (sofern genug Kurshistorie vorliegt) den
  Abschnitt "Robustheit über Teilperioden (Walk-Forward)"
  (`_walk_forward_segmente()`): die Kurshistorie wird in bis zu drei
  gleich große, aufeinanderfolgende Zeiträume geteilt (mindestens 10
  Wochen je Segment, sonst entfällt der Abschnitt komplett — bei
  `_rows()`-großen Test-Fixtures z. B.), und `engine.simulate()` läuft je
  Segment unabhängig mit frischem Startkapital (keine fortgeführte
  Position). Da die Regeln in `scenarios.py` nicht an Daten gefittete
  Parameter haben, ist klassisches Train/Test-Splitting nicht anwendbar;
  stattdessen macht dieser Abschnitt sichtbar, ob eine Strategie über
  verschiedene Marktphasen hinweg ähnlich abschneidet oder ob die
  Gesamtrendite nur aus einer einzelnen guten (oder schlechten) Teilperiode
  stammt — relevant, weil alle Szenarien laut ihrer eigenen Beschreibung
  "erster Ansatz, nicht optimiert/gebacktestet" auf einer einzigen, noch
  kurzen Kurshistorie sind. Die Schwankungsbreite (größte minus kleinste
  Perioden-Rendite) steht als Kennzahl über der Tabelle.
  Zeitraum-Presets (`_zeitraum_presets()`, #54, "Variante B" aus der
  Owner-Entscheidung gegenüber reinem Chart-Zuschneiden): da `docs/*.html`
  statische, ohne Backend gebaute Seiten sind, kann ein Zeitraumfilter nicht
  live neu simulieren — echte Steuer-/Rebalancing-Logik lässt sich nicht in
  JS nachbauen. Stattdessen simuliert der Build je Strategie/Szenario vier
  feste Presets (1/3/5 Jahre zurück ab dem letzten Kurstag via
  `_jahre_zurueck()`, sowie "Gesamte Historie") jeweils VOLLSTÄNDIG NEU
  (frisches Startkapital, analog `_walk_forward_segmente()`) inklusive
  Rendite/Volatilität/Max-Drawdown/Sharpe/Sortino und eigenem
  Wertverlauf-Chart. Auf der Startseite gab es dafür ursprünglich je Strategie/
  Szenario einen eigenen Zeitraum-Umschalter unter dem jeweiligen Chart (#54);
  seit #95 ist das EIN zentraler Schalter (`id="zeitraum-switch"`, in der
  Nähe des Benchmark-Schalters #72 platziert, denselben "ein Schalter steuert
  alle Charts der Seite"-Aufbau nachbildend). Mit #94 gibt es auf der
  Startseite ohnehin keinen Chart mehr je Einzelstrategie (siehe unten,
  `dashboard._rubrik_gruppen()`) — der zentrale Schalter steuert seither ALLE
  Rubrik-Charts gleichzeitig, exakt EIN Schalter für die gesamte Seite statt
  eines Schalters je Rubrik. Nur Preset-Ids, die WIRKLICH JEDE angezeigte
  Strategie/Szenario auch hat, werden als Button angeboten
  (`dashboard._zeitraum_presets_optionen()`); ein Klick ruft
  `applyZeitraumDataset()` für jeden registrierten Chart-Eintrag
  (`zeitraumEntries`, reines Chart.js-Datenwechsel im Browser, kein
  Server-Request) auf, das je Chart nur austauscht, welcher bereits fertig
  simulierte Preset-Datensatz angezeigt wird. Jeder Rubrik-Chart bekommt statt
  einer einzelnen Wertreihe ein `reihen`-Array (eine Reihe je Mitglied, bei
  einer Kombi-Rubrik die Kombi-Strategie zuerst) — `dashboard._rubrik_gruppen()`
  baut das je Preset-Id aus den `zeitraum_presets` ALLER Mitglieder
  (`presets_json`). Der bisherige, auf "erweitert" (#91) beschränkte
  Sondermechanismus des Gruppen-Charts läuft über denselben zentralen
  Mechanismus: `entry.presets['erweitert']` wird von `applyZeitraumDataset()`
  bevorzugt vor der aktuell gewählten Preset-Id angewendet, sobald der
  separate "Verlängerter Auswertezeitraum"-Schalter im Drei-Punkt-Menü aktiv
  ist — aus `view["erweitert"]` (#91, `_erweiterte_kennzahlen()`) je Mitglied,
  da nicht jedes Mitglied notwendigerweise einen eigenen #80-Preset hat. Die
  Detailseite ist von #95 unberührt: sie behält ihren eigenen, lokalen
  Zeitraum-Umschalter
  (`id="detail-zeitraum-switch"`) für den einen dort gezeigten Chart, ergänzt
  um den Abschnitt "Kennzahlen nach Betrachtungszeitraum" (eigener Chart plus
  fünf Kennzahl-Kacheln) — die bislang nur auf der Startseite
  gezeigten Kennzahlen Volatilität/Max Drawdown/Sharpe/Sortino (#40/#41)
  erscheinen dadurch jetzt auch auf der Detailseite, aber ausschließlich
  innerhalb dieses neuen, periodenabhängigen Abschnitts (nicht als
  zusätzliche statische Kachel, siehe die entsprechend angepassten Tests in
  `tests/test_dashboard.py`). Die 50-/200-Tage-Näherung
  (`sma-chart`) bleibt bewusst immer auf der vollen Historie, unabhängig vom
  gewählten Preset, da die gleitenden Durchschnitte selbst ausreichend
  Vorlauf brauchen. "Clientseitig einstellbar" bezieht sich auf die
  Bedienung (Umschalten ohne weiteren Server-Request), nicht auf die
  Berechnung, die vollständig beim Build passiert.
  `templates/praemissen.html.j2` (`docs/praemissen.html`, über das
  Drei-Punkt-Menü von jeder Seite erreichbar) sammelt die Prämissen, auf
  denen alle Zahlen beruhen — seit #93 auch das Kombinationsverfahren
  zusammengesetzter Strategien (`id="kombination"`, Regelliste aus
  `Strategy.beitraege` abgeleitet) und seit #91 die Ersatzbond-Annahme hinter
  dem verlängerten Auswertezeitraum (`id="erweiterter-zeitraum"`) —
  Datenbasis und Zeitraum, Instrumententabelle
  mit **erstem Kurstag je Ticker** (⚠ bei später verfügbaren), Handels- und
  Steuerregeln, die Kennzahl-Definitionen sowie eine explizite Liste des
  nicht Modellierten (jahresgenaue Dividendenhistorien — je Instrument gilt
  ein konstanter Satz aus `Instrument.dividendenrendite`, #74 —, Inflation,
  Spread/Slippage, Depotgebühren/Ausgabeaufschläge, Zinsen auf Cash). Die
  TER steht seit #76 NICHT mehr auf dieser Liste, sie wird modelliert; die
  Seite weist sie je Instrument in der Instrumententabelle aus, ebenso die
  Ausschüttungsrendite (#74) und den je Strategie abgeleiteten risikofreien
  Zins (#75). Ein eigener Abschnitt "Cash und
  ungenutztes Kapital"
  begründet, warum Strategien keine eigene Cash-Zielallokation kennen (Topf
  A übernimmt die Cash-Rolle, #35) und zeigt zusätzlich
  `_cash_anteil_max()` je Strategie/Szenario: den
  größten je erreichten Anteil an technischem `pending_cash`
  (Kapitalanteil ganz ohne handelbares Ziel, #55) samt Datum — in
  `_build_strategy_view()` aus `result.value_history` berechnet und als
  `cash_max_pct`/`cash_max_datum` im View abgelegt, damit die empirische
  Aussage ("Cash kommt praktisch nicht vor") nicht von der Kurshistorie
  abweichen kann. Ganz oben stehen die drei Einschränkungen,
  die schwerer wiegen als jede Renditezahl: Rückschaufehler bei der
  Instrumentenauswahl, nicht optimierte/gebacktestete Regeln, und ein
  einziger Kursverlauf ohne Konfidenzintervalle. `_praemissen_kontext()`
  leitet dafür **alles** aus den tatsächlich verwendeten Konstanten
  (`strategies.py`), aus `instruments.py`, aus der übergebenen Kurshistorie
  und aus den bereits berechneten Strategie-`views` ab (keine zusätzliche
  Simulation) — nach demselben Prinzip wie `learnings.py`: nichts auf der
  Seite ist hinterlegter Text, der gegenüber dem Code veralten könnte. Beim
  Ergänzen deshalb keine Zahl hart ins Template schreiben, sondern über den
  Kontext ziehen.
  **Rückschaufehler mit Hebel (#63, F4):** `build_dashboard()` wendet vor
  jedem `simulate()`-Aufruf zwei Korrekturen auf `rows` an, beide rein in
  der Darstellungsschicht (Owner-Entscheidung zu #63) — `engine.py` bleibt
  unverändert. `_ohne_btc_fruehphase()` entfernt BTC-EUR-Kurse vor einem
  Stichtag (`_BTC_FRUEHPHASE_ENDE`, aktuell 2017-01-01 — Platzhalter nach
  demselben Muster wie `VORABPAUSCHALE_BASISZINS_PLATZHALTER`, kein
  historisch belegtes Datum, sondern die Owner-Einschätzung, ab wann ein
  deutscher Privatanleger realistischen EUR-Zugang zu Bitcoin hatte) und
  nutzt damit denselben, bereits vorhandenen Mechanismus wie ein Instrument
  vor seinem Börsengang, statt eine neue Sonderregel einzubauen.
  `_real_investierbarer_zeitraum()` schneidet die (bereits BTC-bereinigte)
  Historie je Strategie/Szenario auf den Zeitraum zurecht, ab dem ALLE ihre
  Zielinstrumente (`Strategy.alle_ticker_gewichte()`) tatsächlich einen Kurs
  haben — vorher hätte sich `handelbare_gewichte()` in der Frühphase auf die
  anteilige Umlegung auf wenige, früh existierende Instrumente verlassen
  (#55), was für die veröffentlichten Kennzahlen genau die Verzerrung
  erzeugt, die F4 beschreibt (ein 2026 zusammengestelltes Instrumentenset
  rückwirkend ab 2006 zu kaufen ist Look-ahead-Bias, verschärft dadurch, dass
  ein extrem volatiles, früh existierendes Instrument wie Bitcoin in der
  Frühphase ein Vielfaches seines nominalen Zielgewichts hält). Beide
  Funktionen werden je Strategie separat angewendet (unterschiedliche
  Instrumentensets ergeben unterschiedliche Simulationsbeginn-Daten, z. B.
  2021-08-15 für die zwei- und 2021-11-21 für die dreitöpfige
  Barbell-Strategie, Stand der vollen 20-Jahres-Historie) und liefern die je
  Strategie tatsächlich verwendeten `rows` zurück, die anschließend
  überall weiterverwendet werden (Hauptsimulation, Zeitraum-Presets,
  Walk-Forward, Optimierungs-/Beitrags-Effekte) — nicht nur für die
  Startwert-Anzeige. `carry_forward` (#42) bleibt bewusst auf der vollen,
  unveränderten Historie berechnet, da es nur die jüngsten Wochen betrifft.
  Die Prämissen-Seite nennt den Stichtag sowie je Strategie den tatsächlichen
  `sim_beginn` in der Handelsregeln-Tabelle.
  **Strategien ohne Kursdaten (#99):** `_hat_kurshistorie(rows, strategy)`
  prüft, ob es überhaupt EINEN Zeitpunkt gibt, an dem alle Zielinstrumente
  einen Kurs haben; `build_dashboard()` lässt Strategien ohne solchen Zeitpunkt
  komplett weg (kein Tabelleneintrag, keine Detailseite). Ein frisch ergänztes
  Instrument steht bis zum nächsten Kursabruf/Backfill mit leerer Spalte in
  `price_history.csv` — ohne diese Prüfung liefe seine Strategie über die volle
  Historie mit dauerhaft geparktem Kapital (`handelbare_gewichte()` findet kein
  handelbares Ziel) und stünde mit flacher Linie und 0% Rendite in der
  Vergleichstabelle, also mit einer Aussage, die die Daten gar nicht hergeben.
  `_real_investierbarer_zeitraum()` kann das nicht abfangen: es gibt keinen
  Zeitraum, auf den es zuschneiden könnte. Genau das trifft aktuell
  `DIVIDENDE_UND_VALUE`, bis der erste Abruf/Backfill Kurse für `ISPA`/`IS3S`
  geliefert hat.
  **CAGR als Leitkennzahl (#63, F6b/c):** `_cagr_pct(rendite_pct, tage)`
  liefert die annualisierte Rendite aus der Gesamtrendite über die
  tatsächliche Simulationsdauer (`_tage_zwischen()`, Differenz aus erster und
  letzter Kurszeile) — über viele Jahre ist eine Gesamtrendite wie
  "+52.558,53%" praktisch unlesbar, eine annualisierte Zahl lässt sich
  direkt prüfen. `cagr_pct`/`cagr_label` im Strategie-View sind seither die
  Leit-, `rendite_pct`/`rendite_pct_label` die Nebenspalte der
  Übersichtstabelle (dort jetzt nach CAGR statt Gesamtrendite sortiert) —
  dieselbe Umstellung löst zugleich F6c: die Leave-one-out-Differenzen in
  `_optimierungs_effekte()` und den `beitraege` (Börsenweisheiten) liefen
  vorher auf Basis der Gesamtrendite, was bei stark unterschiedlichen
  Größenordnungen zwischen Basis- und Vergleichslauf (z. B. wenn Rebalancing
  eine dominante Position wiederholt auf ihr Zielgewicht zurückführt) keine
  sinnvolle Prozentpunkt-Angabe mehr war; beide Abschnitte rechnen jetzt mit
  CAGR-Differenzen, die auch bei Größenordnungsunterschieden plausibel
  bleiben.
  **Geschätzte Nettorendite (#63, F6a):** `engine.py` führt
  `kumulierte_steuer` bewusst nur als Tracking-Größe und zieht sie nirgends
  vom simulierten Depotwert ab (reines Tracking, siehe engine.py-Abschnitt
  oben) — alle Rendite-/CAGR-Werte sind deshalb Bruttowerte (vor Steuer).
  `_build_strategy_view()` ergänzt daneben eine geschätzte Nettorendite
  (`netto_rendite_pct_label`/`netto_cagr_label`): Endwert minus kumulierte
  Steuer, einmalig am Simulationsende abgezogen. Eine bewusste
  Vereinfachung — reale Steuerzahlungen fallen unterjährig zu
  unterschiedlichen Zeitpunkten an, nicht als einmaliger Abzug am Ende —,
  aber ohne Umbau der Engine (die weiterhin nur Bruttowerte simuliert) die
  einzige Möglichkeit, überhaupt eine Netto-Größenordnung auszuweisen. Beide
  Werte stehen nebeneinander auf jeder Detailseite, mit Fußnote; die
  Übersichtstabelle bleibt bei den (klar so beschrifteten) Bruttowerten.
  **Gemeinsamer Vergleichszeitraum (#73):** `_real_investierbarer_zeitraum()`
  schneidet je Strategie unterschiedlich zu — der S&P-500-Benchmark läuft über
  20 Jahre (ab 2006), jede Barbell-Strategie erst ab 2021. Eine nach CAGR
  sortierte Übersichtstabelle stellte damit Unvergleichbares nebeneinander,
  ausgerechnet in der Zeile, an der jede Anlageentscheidung hängt.
  `_gemeinsamer_beginn()` liefert das späteste Startdatum aller angezeigten
  Strategien, `_vergleichs_cagr_pct()` simuliert jede Strategie ab diesem
  Datum frisch (analog `_walk_forward_segmente()`/`_zeitraum_presets()`);
  `build_dashboard()` legt daraus `vergleich_cagr_*` und `alpha_pp_*` (gegen
  die erste verfügbare Strategie aus `BENCHMARK_STRATEGIEN`) in jede View und
  sortiert die Übersicht danach. Unter `_VERGLEICH_MIN_WOCHEN` entfällt die
  Spalte still, statt aus wenigen Wochen zu annualisieren.
  **Risikokennzahlen ebenfalls aus dem Vergleichszeitraum (#78):** Zunächst
  galt das nur für die Rendite, Volatilität/Max-Drawdown/Sharpe/Sortino blieben
  auf dem eigenen Zeitraum und wurden nur je Zeile gekennzeichnet — das
  reichte nicht. Der Effekt trifft praktisch nur die Benchmark-Zeile (alle
  übrigen Strategien liegen ohnehin fast ganz im gemeinsamen Fenster), war
  dort aber groß genug, die Aussage der Tabelle zu drehen: Max Drawdown des
  S&P-500-Benchmarks −51,95% über die eigene Historie (enthält 2008) gegen
  −21,21% über den gemeinsamen Zeitraum, womit der Index vom scheinbar
  riskantesten zu einem der risikoärmsten wird — aus vermeintlicher Dominanz
  der Barbell-Strategien auf beiden Achsen wird ein echter
  Rendite-Risiko-Tausch. Ein Sharpe-Paarvergleich kippte ebenfalls (0,76 gegen
  0,64 wurde zu 0,66 gegen 0,66), weil für die beiden Zeiträume
  unterschiedliche risikofreie Zinsen gelten (#75). `_vergleichs_cagr_pct()`
  heißt deshalb jetzt `_vergleichs_kennzahlen()` und liefert das komplette
  Bündel (CAGR/Vola/MaxDD/Sharpe/Sortino plus den Zins des Ausschnitts) statt
  nur die CAGR — die Wertreihe des Vergleichslaufs lag ohnehin vor und wurde
  bis dahin verworfen. **Beim Ändern beachten:** die Werte landen unter
  eigenen `vergleich_*`-Feldnamen in der View. Die gleichnamigen Felder ohne
  Präfix beschreiben weiter den eigenen Zeitraum und werden anderswo gebraucht
  (Detailseite: Startwerte der Kacheln in „Kennzahlen nach
  Betrachtungszeitraum"; Prämissen-Seite: risikofreier Zins der eigenen
  Simulation) — sie zu überschreiben verfälscht beide. Die Übersichtstabelle
  zeigt ausschließlich Vergleichszeitraum-Werte (Owner-Entscheidung), die
  Eigen-Zeitraum-Kennzahlen bleiben vollständig auf der Detailseite.
  **Risikofreier Zins (#75):** `_risikofreier_zins_pct(rows)` leitet ihn aus
  dem EUR-Geldmarkt-ETF `_GELDMARKT_TICKER` (`XEON`, durchgehende Historie
  seit 2007) über exakt den ausgewerteten Zeitraum ab, statt ihn zu
  hinterlegen — dasselbe Prinzip wie bei `_praemissen_kontext()`: nichts
  hinterlegen, was gegenüber den Daten veralten kann.
  `_RISIKOFREIER_ZINS_PLATZHALTER` ist nur noch Rückfallwert (fehlender Ticker
  oder Zeitraum unter `_ZINS_MIN_WOCHEN`). **Einheiten-Falle:**
  `_wochenrenditen()` liefert Brüche, `_sharpe_ratio()`/`_sortino_ratio()`
  rechnen intern in Brüchen (trotz der `_pct`-Namen), der Zins kommt dagegen
  in Prozentpunkten herein und wird vor dem Abzug durch 100 geteilt.
- `learnings.py` — leitet die Sektion "Key Learnings" (ganz oben im Dashboard)
  bei jedem Build neu aus den Strategie-Views ab. **Keine hinterlegten
  Erkenntnis-Texte:** fest ist nur die Fragestellung je Regel (reine Funktion
  `(views) -> Learning | None`), alle Zahlen *und* Superlative ("größter
  Bremsklotz", "einziger Rückhalt") kommen aus den aktuellen Ergebnissen.
  Liefert eine Regel `None`, ist ihre Frage aus den Daten nicht beantwortbar
  (z. B. Vergleichsaussagen bei nur einer Strategie) — das Learning fällt dann
  still weg, statt eine Aussage zu erfinden; bei leerer Liste rendert das
  Template die Sektion gar nicht. Beim Ergänzen einer Regel: nichts in den
  Fließtext schreiben, was nicht aus `views` belegt ist, und die
  Deutsch-Formatierung über `_zahl()/_pp()/_pct()/_eur()` laufen lassen (ein
  `.replace(".", ",")` auf dem ganzen Satz erwischt sonst den Satzpunkt).
  **Drei kleinere Startseiten-Ergänzungen (#79/#81/#82), alle rein in der
  Darstellungsschicht, keine neue Simulation:**
  - `common_context["portfolio_instrumente"]` (#79) listet ALLE
    `instruments.TICKERS` (nicht nur die allokierten, anders als
    `instrumente_anzahl`/#66) mit Ticker und ausgeschriebenem Namen in einer
    Tabelle im Abschnitt „Das Portfolio" (seit #88 auf `docs/portfolio.html`
    statt auf der Startseite) — beantwortet direkt
    "was wird da eigentlich wöchentlich abgerufen?", unabhängig davon, ob ein
    Instrument aktuell in einer Strategie/einem Szenario steckt.
  - Info-Tooltips (#81): jede Kennzahl-Spaltenüberschrift der
    Übersichtstabelle (CAGR, Überrendite, Volatilität, Max Drawdown, Sharpe,
    Sortino, Gesamtrendite, Eigener Zeitraum) bekommt ein `<span
    class="info-tip" title="...">i</span>` mit Erklärungstext — natives
    `title`-Attribut statt JS-Tooltip (kein zusätzliches Skript, per
    Tastatur/Screenreader über `tabindex="0"` erreichbar). Die Tooltip-Texte
    dürfen das Wort "Vergleichszeitraum" nicht enthalten, wenn sie außerhalb
    des `{% if vergleich_verfuegbar %}`-Blocks stehen — ein Test prüft, dass
    ohne gemeinsamen Vergleichszeitraum dieses Wort in der gesamten Tabelle
    nirgends vorkommt (`test_ohne_gemeinsamen_zeitraum_...`), ein zu
    ausführlicher Tooltip-Text an einer immer gerenderten Spalte hätte das
    sonst unbeabsichtigt verletzt.
  - CAGR-Balkendiagramm der Startseite (#92): zeigt nur Strategien mit
    `Strategy.im_startseiten_chart` (Default `True`, rein darstellerisch).
    Mit allen 16 Läufen dünnte Chart.js die Achsenbeschriftungen aus — es
    standen mehr Balken da als Namen. Abgewählt sind die Börsenweisheiten (die
    direkt darunter ihren eigenen Gruppen-Chart haben), „Barbell 20/80 (breiter
    diversifiziert)" und der Cost-Average-Einstieg; sie bleiben in der
    Vergleichstabelle, mit Detailseite und Wertverlauf-Chart. Zusätzlich
    `autoSkip: false`, damit jeder verbleibende Balken seinen Namen behält.
  - Gruppen-Chart der Börsenweisheiten (#90/#93): sechs Linien brauchen sechs
    Farben — `--series-5/6/7` ergänzt (hell und dunkel), `--series-4` bleibt
    der Benchmark-Overlay-Linie vorbehalten, die im selben Chart liegt. Der
    Chart hängt seit #93 selbst am Benchmark-Schalter (#72); dessen
    `applyBenchmarkDataset()` schreibt die Overlay-Linie deshalb hinter
    `chart.__baseDatasetCount` statt fest auf Position 1, sonst würde sie ein
    Mitglied überschreiben. Der Schalter steht jetzt ÜBER dem Abschnitt.
  - **Rubriken statt eines Charts je Strategie (#94):** Die Startseite zeigt
    keinen einzelnen Wertverlauf-Chart mehr je Strategie/Szenario. Optionales
    Feld `Strategy.rubrik` (`strategies.RUBRIK_BARBELL`/`RUBRIK_BOERSENWEISHEITEN`/
    `RUBRIK_CHARTTECHNIK`/`RUBRIK_WEITERE_ANALYSEN`/`RUBRIK_REFERENZ`) gruppiert
    Strategien/Szenarien zu fünf Rubriken; `dashboard._rubrik_gruppen()`
    (generalisiert das bisherige `_teilszenario_gruppen()`, das damit entfällt)
    baut je Rubrik EINEN kombinierten Vergleichs-Chart (alle Mitglieder als
    eigene Linie) plus eine `<ul>`-Aufzählung mit Kurzbeschreibung + Link zur
    Detailseite je Mitglied — ersetzt dort den vorherigen Chart-je-Strategie-
    Abschnitt. Detailseiten (inkl. Zeitraum-Umschalter, `own_chart_max`,
    `eigene_chart_skala`) bleiben unverändert, nur die Startseite fasst
    zusammen. Fällt `Strategy.rubrik` weg (z. B. in Tests mit einer eigenen
    Ad-hoc-Strategieliste), bildet jede Strategie ohne `rubrik`/`teil_von`
    ihre eigene Einzel-Rubrik unter ihrem Namen — das erhält für solche Aufrufe
    weiterhin einen (Einzel-)Chart pro Strategie, ohne dass Tests künstlich
    eine der fünf produktiven Rubriken tragen müssten. Der S&P-500-Benchmark
    bekommt bewusst eine eigene Rubrik `RUBRIK_REFERENZ` statt der
    Barbell-Rubrik (Owner-Entscheidung): er ist strukturell kein Barbell,
    sondern eine reine Vergleichslinie. Zusammengesetzte Strategien mit
    `Strategy.beitraege` (aktuell nur die Börsenweisheiten-Kombi) behalten
    innerhalb ihrer Rubrik die bisherige Kombi/Kind-Optik (dicke Linie für die
    Kombi, dünne für die einzelnen Teilregeln, Verweis auf
    `praemissen.html#kombination`) — `_rubrik_gruppen()` erkennt das an
    `Strategy.beitraege` und sortiert die Kombi-Strategie dafür an die erste
    Stelle der Mitgliederliste, unabhängig von ihrer Position in
    `STRATEGIES`/`SCENARIOS` (in `scenarios.SCENARIOS` steht die Kombi bewusst
    NACH ihren fünf Kindern). `wertChartMax`/`wertChartScales`
    (gemeinsame Y-Achse über ALLE Strategien, #24) sowie der zugehörige
    Kontext-Wert `wert_chart_max` sind dadurch entfallen — jede Rubrik
    skaliert jetzt unabhängig über ihr eigenes `chart_max`, ein Ausreisser
    (Detail siehe #95-Abschnitt weiter oben zum zentralen Zeitraum-Schalter,
    der seit dem Zusammenführen von #94 und #95 direkt auf den Rubrik-Charts
    statt auf Einzelstrategie-Charts arbeitet)
    (früher `eigene_chart_skala`) kann die Skala einer fremden Rubrik gar
    nicht mehr erreichen.
  - Korrelationsgrafik (#82): ein Chart.js-Scatter-Chart
    (`#correlation-chart`, seit #88 auf `docs/vergleich.html` statt auf der
    Startseite), x-Achse
    CAGR % p.a., y-Achse Max Drawdown % (negativ dargestellt, wie in der
    Tabelle) — je Strategie/Szenario ein Punkt, aus denselben `summary`-Werten
    wie die Übersichtstabelle (Vergleichszeitraum-Werte, falls verfügbar,
    sonst der jeweils eigene Zeitraum). Macht den Rendite-Risiko-Tausch, den
    die Tabelle nur spaltenweise zeigt, auf einen Blick sichtbar.
  **Längerer Betrachtungszeitraum per Ersatzbond-Annahme (#80), zusätzlicher
  Preset statt Ersatz der bestehenden Zeiträume:** `_real_investierbarer_
  zeitraum()` (F4/#63) schneidet die Historie je Strategie auf den Zeitpunkt
  zurecht, ab dem ALLE Zielinstrumente handelbar sind — das kostet z. B. bei
  der dreitöpfigen Barbell-Strategie rund 15 Jahre Historie (Start erst 2021
  statt 2006). Issue #80 bittet ausdrücklich um einen längeren Zeitraum, mit
  der Annahme, dass das Kapital eines noch nicht handelbaren Zielinstruments
  bis zu dessen Verfügbarkeit in einem einzigen, für ALLE Strategien/Szenarien
  GLEICHEN Anleihe-ETF angelegt war ("nicht unterschiedliche Bonds", explizite
  Vorgabe im Issue). `_ERSATZBOND_TICKER = "IBCL"` (Euro-Staatsanleihen
  15–30 Jahre, Historie ab 2007-05-18, die längste unter den Anleihen-ETFs) ist
  die Wahl — bewusst eine echte Anleihe, nicht `_GELDMARKT_TICKER` (XEON), das
  als Cash-Äquivalent für den risikofreien Zins (#75) dient.
  `_mit_ersatzbond(strategy)` erweitert eine Strategie um diese Annahme über
  `Strategy.gewichte_fn` (dieselbe Erweiterungsstelle wie `scenarios.py`, kein
  Engine-Eingriff): das Ziel-Gewicht jedes in der aktuellen Zeile noch nicht
  handelbaren Instruments wandert auf `_ERSATZBOND_TICKER`, statt wie in
  `handelbare_gewichte()` anteilig auf die ÜBRIGEN Zielinstrumente verteilt zu
  werden — genau das war die in F4 beschriebene Überkonzentration (v. a.
  Bitcoin in der Frühphase). Der Ersatzticker muss dafür Teil von
  `Strategy.alle_ticker_gewichte()` sein, sonst würde `engine.
  rebalance_to_targets()` das umgeleitete Gewicht stillschweigend verwerfen
  (iteriert nur über die beim Start fixierten `tickers`, siehe die
  "Werterhaltung beim Rebalancing"-Erklärung zu `engine.py` oben) — ist der
  Ersatzticker noch nicht Teil der Strategie, ergänzt `_mit_ersatzbond()` einen
  zusätzlichen Topf mit `gewicht_gesamt=0`, ohne die eigentlichen
  Topf-Zielgewichte zu verändern; ist er es schon (z. B. würde er es bei
  künftigen Strategien sein), wird kein Topf ergänzt (sonst gäbe es den
  Ticker doppelt, was `Strategy.topf_von()` nicht vorsieht).
  `_zeitraum_presets()` bekommt dafür einen optionalen dritten Parameter
  `erweiterte_rows` (in `build_dashboard()`: `rows_ohne_btc_fruehphase`, also
  die volle, nur um die BTC-Frühphase bereinigte Historie, gemeinsam für alle
  Strategien) und hängt bei tatsächlichem Gewinn an Historie
  (`erweiterte_rows[0].date < rows[0].date`) einen fünften Preset
  `"erweitert"` ("Erweitert (Ersatzbond-Annahme)") an — simuliert mit
  `_mit_ersatzbond(strategy)` über `erweiterte_rows`. Bewusst NUR bei
  tatsächlichem Gewinn angeboten (z. B. nicht bei `SP500_BENCHMARK`, dessen
  einziges Instrument `IUSA` fast die gesamte Historie abdeckt) — sonst wäre
  der Preset nur eine redundante Kopie von "Gesamte Historie". Weil der neue
  Preset über den generischen `s.zeitraum_presets`-Loop in
  `dashboard.html.j2`/`strategy_detail.html.j2` gerendert wird, war dafür
  KEINE Template-Änderung nötig — nur ein zusätzlicher Hinweistext auf der
  Detailseite, der erklärt, was die Annahme bedeutet. Ändert nichts an der
  primären "Eigener Zeitraum"/"Vergleichszeitraum"-Darstellung (#73/#78) oder
  an bestehenden Tests — rein additiv.

### Kursquelle wechseln

Jede Quelle liefert nur `dict[ticker, PriceQuote]` an
`history_store.record_week()` — Engine/Dashboard/Tests sind davon
unabhängig. Der frühere manuelle Weg über `scripts/record_prices.py`
(Cowork/Websuche) wurde in #51 gestrichen: der Kursabruf läuft konsequent
über GitHub Actions (`scripts/run_fetch.py`, `AlphaVantageSource`). Ein
Wechsel der Kursquelle bedeutet seither, eine neue `PriceSource`-
Implementierung in `sources/` zu ergänzen und `run_fetch.py` darauf
umzustellen — nicht mehr, den automatisierten Abruf durch einen manuellen
Prompt-/Agentenweg zu ersetzen.

### Historischer Backfill

`data/price_history.csv` wächst im Normalbetrieb nur Woche für Woche seit
Projektstart, weil `GLOBAL_QUOTE` nur den aktuellen Kurs liefert.
`scripts/backfill_history.py` nutzt stattdessen `TIME_SERIES_WEEKLY_ADJUSTED` /
`DIGITAL_CURRENCY_WEEKLY` (komplette verfügbare Historie in **einem**
Request pro Ticker) und schreibt über `history_store.record_week()`
(derselbe Pfad wie der Live-Abruf) die komplette Historie neu. USD-Ticker
werden mit dem historischen `FX_WEEKLY`-Kurs derselben Woche umgerechnet
(Forward-Fill bei fehlender Woche).

**Splitbereinigung (#62):** Der Backfill lief bis dahin über
`TIME_SERIES_WEEKLY`, also über *nominale* Schlusskurse. Jeder Aktiensplit
sieht dort wie ein Kurssturz aus — in der 20-Jahres-Historie betraf das fünf
der zehn Satelliten-Aktien mit Phantom-Wochenverlusten bis -91% (TSLA 5:1
2020 und 3:1 2022, MSTR 10:1 2024, KO 2:1 2012, RHHBY und BYDDY je ein
ADR-Verhältniswechsel). `_split_bereinigte_close_series()` leitet aus
`close / adjusted close` den kumulierten **Split**-Faktor ab und teilt die
Nominalkurse dadurch. Bewusst split-only statt des vollen `adjusted close`:
der ist eine Total-Return-Reihe und enthält auch Dividenden, die die
Simulation bereits separat als Barertrag modelliert (`ausschuettend` /
`Instrument.dividendenrendite`, #57/#74) — sonst zählten sie doppelt. Liefert
ein Symbol gar keinen `adjusted close`, bleibt es beim Nominalkurs.

**Handgepflegte Ergänzungen (#62):** Der Backfill setzt
`price_history.csv` komplett zurück (`_reset_data_files()`) — manuell
nachgetragene Kurse wären damit bei jedem Lauf weg. Zwei Dateien werden
deshalb **nur gelesen und von keinem Skript geschrieben**, und bei jedem
Lauf neu eingemischt:

- `data/manual_fx_usd_eur.csv` (`Date,EUR_pro_USD`) — gelesen von
  `read_manual_fx()`, eingemischt **vor** der Währungsumrechnung. Das ist
  der wirksamste Ort für Handarbeit: eine gepflegte Zeile macht die Woche
  für alle neun USD-Ticker *und* für BTC-EUR umrechenbar, deckt also den
  gesamten Bereich ab, den `FX_WEEKLY` vor November 2014 offenlässt.
  Eingecheckt sind bereits die offiziellen **EZB-Referenzkurse** (Euro
  foreign exchange reference rates) für 2006-01-02 bis 2014-11-14, 2.272
  Einträge in **täglicher** Auflösung — damit wird jeder Wochenschlusskurs
  mit dem Kurs seines eigenen Handelstags umgerechnet statt mit dem einer
  benachbarten Woche. Bewusst nur bis KW 46/2014: ab KW 47 übernimmt
  `FX_WEEKLY`, dessen Daten dadurch unangetastet bleiben (der Lauf meldet
  entsprechend „0 ersetzt"). Die Datei trägt Quelle, Abdeckung und
  Kontrollwerte in ihrem Kopf; sie wurde aus der in `currencyconverter`
  (PyPI) gebündelten Kopie der EZB-Datei erzeugt, weil der Direktabruf bei
  `data-api.ecb.europa.eu` an der Netzwerk-Policy scheitert.
- `data/manual_prices.csv` (`Date,Ticker,Preis_EUR`, Langformat) — gelesen
  von `read_manual_prices()`, eingemischt **ganz zum Schluss**. Die Werte
  sind deshalb **immer schon in EUR** und laufen nicht mehr durch die
  Umrechnung. Gedacht für Kurse, die Alpha Vantage überhaupt nicht liefert
  (z. B. die früheste BTC-Historie) — nicht für USD-Kurse, die nur am
  fehlenden Wechselkurs scheitern; die gehören in die FX-Datei.

Beide Dateien erlauben `#`-Kommentarzeilen und dokumentieren sich damit
selbst. Ein unbekannter Ticker in `manual_prices.csv` bricht den Lauf ab,
statt still ignoriert zu werden. Handgepflegte Werte gewinnen gegen die
API; verglichen wird auf **ISO-Wochen-Ebene** (`_ueberschreibe_iso_woche()`),
nicht auf Datumsebene — sonst stünde ein manueller Freitagswert neben dem
API-Donnerstagswert derselben Woche und welcher in die Zeile käme, wäre
Zufall der Iterationsreihenfolge. Der Lauf gibt je Datei aus, wie viele
Wochen ergänzt und wie viele ersetzt wurden; Einträge sollten wieder
entfernt werden, sobald die API die Woche selbst liefert.
`collect_weekly_series()` bleibt dateizugriffsfrei — die beiden Dicts
kommen als Parameter herein, gelesen wird in `main()`.

**Keine Rückwärts-Extrapolation des Wechselkurses (#62):**
`_nearest_fx_rate()` fiel für Wochen *vor* Beginn der FX-Reihe auf den
ältesten verfügbaren Kurs zurück — der aber jünger ist als das umzurechnende
Datum. Alpha Vantages `FX_WEEKLY` liefert USD/EUR erst ab **November 2014**;
dadurch wurden im 20-Jahres-Backfill 227 Wochen (Juli 2010 bis November 2014)
aller USD-Ticker *und* die komplette frühe BTC-Historie mit dem konstanten
Kurs 0,7982 EUR/USD von 2014 umgerechnet, die Wechselkursbewegung dieser
Jahre fehlte also vollständig. Jetzt liefert die Funktion für solche Wochen
`None`, `record_week()` trägt „missing" ein (dieselbe Regel, der
`AlphaVantageSource.fetch()` beim Live-Abruf schon folgt), und `_fx_luecken()`
meldet jeden Zeitraum ohne Abdeckung. Diese Prüfung schaut bewusst nicht nur
auf den **Beginn** der Reihe, sondern auch auf Löcher **mittendrin**: sobald
`manual_fx_usd_eur.csv` die Frühphase abdeckt, startet die Reihe 2006, und
eine reine Beginn-Prüfung würde ein Loch zwischen dem Ende der gepflegten
Daten und dem Beginn der API-Abdeckung nicht mehr sehen — genau das passiert,
wenn Alpha Vantages FX_WEEKLY-Fenster mit der Zeit nach vorne wandert.
Toleranz `_FX_LUECKE_TAGE = 14`, damit einzelne Feiertagswochen und der
Versatz zwischen `since` (heute minus N Jahre) und dem ersten Handelstag
keine Fehlalarme auslösen. **Folge:** Ohne die EZB-Ergänzungsdatei würde ein
Re-Backfill die belastbare Historie der 9 USD-Ticker und von BTC-EUR auf
November 2014 verkürzen; mit ihr bleibt die volle Historie erhalten,
umgerechnet mit dem jeweils zeitgleichen echten Wechselkurs. Reine Datenbeschaffung
(`collect_weekly_series`) und CSV-Schreiben (`write_backfilled_history`)
sind als separate, unabhängig testbare Funktionen im Skript
implementiert - siehe `tests/test_backfill_history.py` (mockt
`AlphaVantageSource`, kein echter Netzwerkzugriff). **Stand:** Alle drei Endpunkte
(`TIME_SERIES_WEEKLY`/`FX_WEEKLY`/`DIGITAL_CURRENCY_WEEKLY`) sind im
Free-Tier verfügbar und alle 17 Symbol-Mappings lösen auf — belegt durch
den Lauf vom 18.08.2026. Der Lauf brach dennoch ab, weil der
Zeitreihen-Schlüssel für `FX_WEEKLY` falsch angenommen war; Alpha Vantage
benennt ihn je Endpunkt unterschiedlich (`Weekly Time Series` /
`Time Series FX (Weekly)` / `Time Series (Digital Currency Weekly)`).
`_extract_time_series()` rät den Namen deshalb nicht mehr, sondern nimmt
den einzigen Objekt-Wert der Antwort außer `Meta Data` — Fehler- und
Rate-Limit-Antworten haben nur String-Werte und lösen damit automatisch
eine aussagekräftige Exception aus. Der FX-Abruf läuft bewusst **vor** den
Ticker-Abrufen, damit ein Fehlschlag einen statt 17 Requests kostet. Für den Lauf gibt es den manuell startbaren
Workflow `.github/workflows/backfill.yml` (nutzt das Repo-Secret, verlangt
`confirm=REPLACE`, schreibt eine Plausibilitätsprüfung in die Job-Summary) -
nicht am selben Tag wie den wöchentlichen Kursabruf starten (beide zusammen
reißen das Tageslimit 25). Seit #99 hat der Workflow zusätzlich einen
`batch`-Input (1 / 2 / „alle"): ein vollständiger Backfill aller 26 Ticker
braucht 27 Requests und läuft deshalb als ZWEI Läufe an zwei
aufeinanderfolgenden Tagen — Batch 1 setzt `price_history.csv` zurück und baut
neu auf, Batch 2 mischt seine Ticker über denselben `record_week()`-Merge
additiv dazu. Die Reihenfolge ist bindend: Batch 2 zuerst ließe die Historie
der Batch-1-Ticker leer. Nach Tag 1 ist die Historie bewusst unvollständig. `--years` ist nur eine untere Schranke, die
`AlphaVantageSource.fetch_weekly_history()`/`fetch_crypto_weekly_history()`
zum Filtern der von Alpha Vantage gelieferten Zeitreihe nutzen - ein Wert,
der weiter zurückliegt als die tatsächlich verfügbare Historie eines
Tickers, liefert einfach dessen komplette verfügbare Historie statt eines
Fehlers. Default (Skript und Workflow-Input) ist deshalb bewusst 20 statt
5: zielt auf "so weit wie möglich" statt auf einen Zeitraum, der
zur jüngsten Satellit-Position passt (Rivian, IPO November 2021) - ältere
Instrumente (ETFs, Coca-Cola, Roche) haben bei Alpha Vantage oft 15-20+
Jahre Historie. Für Ticker ohne Kurs in einer früh liegenden Woche trägt
`history_store.record_week()` ohnehin "missing" statt eines erfundenen
Werts ein, dieselbe Lücken-Behandlung wie beim laufenden Live-Abruf.

### GitHub Actions (`.github/workflows/weekly-update.yml`)

Läuft seit #99 an ZWEI aufeinanderfolgenden Tagen (Sonntag und Montag, je
06:00 UTC) + `workflow_dispatch` (mit Auswahl-Input `batch`): Tests →
Kursabruf (Alpha Vantage, `run_fetch.py --batch N`) → Dashboard-Build →
Commit von `data/price_history.csv`/`data/fetch_log.csv` zurück ins Repo →
GitHub-Pages-Deploy. Welcher Batch läuft, leitet der Workflow aus
`github.event.schedule` ab (Sonntags-Cron → Batch 1, sonst Batch 2); bei
manuellem Start entscheidet der Input. **Sonntag+Montag statt Montag+Dienstag
(wichtig beim Verschieben der Cron-Zeiten):** einsortiert werden die Kurse über
den von der Quelle gemeldeten HANDELSTAG, nicht über das Abrufdatum
(`row_date_from_quotes()`). Beide Läufe liegen so vor dem Xetra-Handelsbeginn
am Montag und sehen denselben letzten Handelstag (Freitag) — und landen damit
in derselben ISO-Woche, die `record_week()` dann additiv zusammenführt. Ein
Dienstagslauf sähe bereits den Montagsschluss, also die FOLGEWOCHE, und die
Ticker seines Batches hingen dauerhaft eine Woche hinterher. Braucht die Secrets/Settings: Repo-Secret
`ALPHAVANTAGE_API_KEY`; Settings → Actions → Workflow permissions → "Read
and write permissions"; Settings → Pages → Source → "GitHub Actions".
**Der Commit-Schritt trägt bewusst `continue-on-error: true`:** ein
zeitgleicher Merge auf `main` (z. B. während der Workflow läuft) kann den
`git push` der Kurshistorie in einen echten Merge-Konflikt laufen lassen
(beobachtet am 22.08.2026, Lauf #16 — Fetch und Dashboard-Build liefen
beide durch, nur der anschließende Push/Rebase scheiterte). Ohne
`continue-on-error` riss das den gesamten Job ab und übersprang damit
„Pages-Artefakt hochladen" sowie den `deploy`-Job — ein für den Pages-Deploy
irrelevanter git-Konflikt verhinderte so die Veröffentlichung eines bereits
fertig gebauten Dashboards. `record_week()` ist wochen-idempotent (siehe
`history_store.py`), ein bei einem Konflikt verpasster Commit holt sich beim
nächsten erfolgreichen Lauf von selbst nach — es geht keine Kurswoche
verloren, nur die Veröffentlichung dieses einen Laufs würde sonst unnötig
blockiert.

### Tests

`tests/test_engine.py` verifiziert die Simulation gegen von Hand
vorgerechnete Werte (nicht nur Smoke-Tests) für zwei unterschiedliche
Strategien plus Determinismus (zweifacher Lauf → identisches Ergebnis).
Eigener Abschnitt für die `Optimierungen`-Schalter (#17): je Schalter ein
Test, dass sein Ausschalten den erwarteten Effekt hat (keine Gebühren in den
Trades, keine `rebalance`-Trades, keine Dezember-Harvest-Trades, Steuerstatus
bleibt bei den Defaultwerten), plus ein Test, dass ein explizit übergebenes
`Optimierungen()` (alle Defaults) exakt dasselbe Ergebnis liefert wie gar
keine Übergabe, und dass `Strategy.optimierungen` ohne Override greift.
Eigener Abschnitt für die Steuerkorrekturen (#37/#38/#39): je ein von Hand
nachgerechneter Test für Teilfreistellung (EUNL/4GLD-Rebalance, Freibetrag
sinkt nur um 70% des Rohgewinns), Vorabpauschale (EUNL ohne jeden Verkauf,
Freibetrag sinkt trotzdem am Jahresende) sowie BTC-Spekulationsfrist einmal
innerhalb (Gewinn landet in der Freigrenze, Sparerpauschbetrag bleibt
unangetastet) und einmal außerhalb der Frist (großer Gewinn bleibt komplett
steuerfrei, andernfalls wäre eine deutliche Steuer sichtbar). Eigener
Abschnitt für die "5/25-Regel je Topf" (#63, F5): ein von Hand nachgerechneter
Drei-Töpfe-Test, dass ein Topf abseits von `ziel_topf` allein ein Rebalancing
auslöst (der alte, nur-ziel_topf-Trigger hätte hier nicht ausgelöst), sowie
ein Test-Paar mit identischem Kursverlauf, das mit gesetztem
`rebalancing_schwelle_relativ=0.25` ein Rebalancing über die relative Schwelle
auslöst und mit dem Default (`1`, effektiv deaktiviert) exakt das Verhalten
vor #63 reproduziert (kein Rebalancing).
`tests/test_history_store.py` prüft Wochen-Idempotenz, Carry-Forward und
`read_fetch_log()`; seit #99 zusätzlich die additiven Teilabrufe: ein zweiter
Batch derselben Woche darf die Kurse des ersten nicht auf die Vorwoche
zurückwerfen, protokolliert nur seine eigenen Ticker, hält das frühere
Zeilendatum — und ein Ticker, der in KEINEM Batch war, fällt weiterhin auf den
alten Carry-Forward zurück (Sicherheitsnetz).
`tests/test_dividende_value.py` prüft die #99-Strategie: Registrierung und
Rubrik, Gewichte summieren zu 1, zwei gleich große Töpfe, 5/25-Regel,
Symbol-Mapping vorhanden und EUR/XETRA-notiert, die belegten Steuerattribute
(ISPA ausschüttend mit eigenem Satz, IS3S thesaurierend, beide 30%
Teilfreistellung), End-to-End-Lauf sowie ein Regressionstest analog zu #64,
dass keine bestehende Strategie die zwei neuen Instrumente hält.
`tests/test_sources.py` / `tests/test_alphavantage.py` mocken die jeweilige
Provider-API vollständig (kein echter Netzwerkzugriff in Tests).
`tests/test_scenarios.py` verifiziert die generische `gewichte_fn`-Mechanik
in der Engine anhand handgerechneter Werte sowie die konkreten Szenarien
(Sell in May, Buy & Hold, SMA-Crossover inkl. der verkürzten 4/20-Wochen-
Variante aus #28) als End-to-End-Smoke-Tests. Für das
kombinierte Börsenweisheiten-Szenario sind die gemittelten Quoten handgerechnet
(Mai → 40%, Dezember → 87,5%) sowie geprüft, dass die Gewichte in jeder Woche zu
1 summieren und jede Leave-one-out-Variante genau eine Weisheit weglässt.
`tests/test_dashboard.py` prüft seit #31 explizit die Trennung zwischen
Startseite und Detailseite: Wertverlauf-Chart und Übersichtstabelle (mit
Links auf `<slug>.html`) bleiben auf der Startseite, Kennzahl-Kacheln,
Topf-Gewichtung, Instrumententabelle sowie die Beitrags-/Optimierungs-
Effekte-Abschnitte erscheinen nur auf der jeweiligen `<slug>.html`; der
Beitrags-Abschnitt wird dort weiterhin nur bei gesetztem `beitraege`
gerendert. Seit #30 zusätzlich: `_teilszenario_gruppen()` gegen eine triviale
Kombi-plus-zwei-Kinder-Fixture (Kombi-Strategie plus zwei `teil_von`-
Unterszenarien) - der Gruppen-Chart auf der Startseite enthält alle drei als
Datasets, und ohne gesetztes `teil_von` erscheint gar kein Gruppen-Abschnitt.
Seit #40/#41/#42 zusätzlich: Volatilität/Max-Drawdown-Funktionen
gegen handgerechnete Kursreihen (konstant/monoton/auf-und-ab), die
Konzentrationswarnung anhand einer Strategie mit einem einzelnen Topf (in
dem der Topf-Trigger nie greift, weil der Topf immer 100% hält, während
sich die Instrumente darin frei auseinanderentwickeln) sowie die
Eingefroren-Markierung anhand eines übergebenen `fetch_log`. Zusätzlich:
Sharpe/Sortino gegen handgerechnete Grenzfälle (konstante Reihe → 0.0,
nur Gewinnwochen → Sortino bewusst 0.0 statt undefiniert,
`_downside_deviation()` ignoriert nachweislich Streuung nach oben) sowie
`_walk_forward_segmente()` gegen eine lange synthetische Kursreihe (leer
bei zu wenig Wochen, exakt drei Segmente bei ausreichender Historie) und
End-to-End, dass der Detailseiten-Abschnitt "Robustheit über Teilperioden"
nur bei genug Kurshistorie erscheint. Für die Zeitraum-Presets (#54):
`_jahre_zurueck()` gegen den Normalfall sowie den Schaltjahr-Sonderfall
(29.02. minus 1 Jahr fällt auf den 28.02. zurück), `_zeitraum_presets()`
gegen eine mehrjährige synthetische Kursreihe (alle vier Presets vorhanden,
kürzerer Zeitraum liefert eine andere Rendite als die volle Historie, der
"alle"-Preset entspricht exakt einer normalen `engine.simulate()` über die
komplette Historie) sowie End-to-End, dass Start- und Detailseite den neuen
Umschalter/Abschnitt rendern. Die beiden älteren Tests, die Volatilität/Max
Drawdown/Sharpe/Sortino als *ausschließlich* auf der Startseite vorkommend
geprüft hatten, wurden dafür angepasst (`test_risikokennzahlen_
ausserhalb_des_zeitraum_abschnitts_nur_auf_startseite`/`test_sharpe_
sortino_ausserhalb_des_zeitraum_abschnitts_nur_auf_startseite`): diese
Kennzahlen erscheinen auf der Detailseite jetzt genau einmal, aber nur
innerhalb des neuen periodenabhängigen Abschnitts. Für die Prämissen-Seite: dass sie
erzeugt und von Start- *und* Detailseite verlinkt wird, dass ihre Werte
tatsächlich aus `ORDERGEBUEHR`/`SPARERPAUSCHBETRAG_PRO_JAHR`/`TICKERS` und
der übergebenen Historie stammen (statt hart im Template zu stehen), und
dass die wesentlichen Einschränkungen samt Platzhalter-Kennzeichnung
benannt sind, sowie dass der Cash-Abschnitt für eine Strategie ohne jemals
ungenutztes Kapital "0.0" und für eine Strategie mit durchgehend
fehlendem Zielinstrument den korrekten Cash-Höchststand samt Datum zeigt.
Für F4/F6 (#63): `_cagr_pct()` gegen handgerechnete Grenzfälle (eine exakte
Verdopplung über vier Jahre ergibt 100% CAGR, Totalverlust ergibt -100%,
`tage=0` ergibt 0%), dass die Übersichtstabelle sowohl die CAGR- als auch die
Gesamtrendite-Spalte zeigt, `_ohne_btc_fruehphase()` gegen eine Zeile vor und
eine ab dem Stichtag (nur BTC-EUR wird vor dem Stichtag entfernt, andere
Ticker bleiben unangetastet), `_real_investierbarer_zeitraum()` gegen eine
Historie mit einem zunächst fehlenden Instrument (schneidet exakt auf den
ersten vollständigen Zeitpunkt zu) sowie den Grenzfall, dass eine Strategie
mit einem nie handelbaren Instrument die Historie unverändert zurückbekommt,
und dass die geschätzte Nettorendite unter der Bruttorendite liegt, sobald
über einen hand-konstruierten großen Rebalancing-Gewinn tatsächlich Steuer
anfällt. Für #66: `_allokierte_ticker()` gegen eine Fixture, die nur einen
Ticker hält, unabhängig davon wie viele `instruments.py` insgesamt kennt;
dass die Startseite die dynamische Instrumentenzahl statt der fest
eingetragenen „17" zeigt; dass die Prämissen-Seite allokierte und nicht
allokierte Instrumente in getrennte Tabellen/Abschnitte einsortiert; und
dass der „Datenreihen ohne Allokation"-Abschnitt gar nicht erst gerendert
wird, wenn eine Strategie ausnahmsweise alle Ticker hält.
Für den gemeinsamen Vergleichszeitraum (#73): `_gemeinsamer_beginn()` gegen
zwei Strategien mit unterschiedlichem Startdatum, `_vergleichs_cagr_pct()`
gegen eine Reihe, die erst spät zu steigen beginnt (der Ausschnitt muss eine
deutlich andere CAGR liefern als die volle Historie) sowie den Grenzfall unter
`_VERGLEICH_MIN_WOCHEN` (liefert `None`), und End-to-End, dass die
Übersichtstabelle Vergleichszeitraum plus beide eigenen Zeiträume rendert —
und dass die Überrendite-Spalte ohne Benchmark-Strategie unter den angezeigten
Strategien gar nicht erst erscheint. Für den risikofreien Zins (#75):
`_risikofreier_zins_pct()` gegen eine konstruierte 4%-Geldmarktreihe, gegen
eine Historie ganz ohne `XEON` und gegen einen zu kurzen Zeitraum (beide →
Platzhalter), dass ein positiver Zins Sharpe *und* Sortino gegenüber 0 senkt
(vorher gar nicht möglich) und dass ein Aufruf ohne Zinsangabe unverändert den
Platzhalter nutzt. In `tests/test_engine.py` für #74: dass die sieben
Nicht-Zahler `ausschuettend=False` sind und ohne Dividende auch keinen
Cash-Zufluss bekommen, dass zwei Instrumente mit unterschiedlicher Rendite
unterschiedlich viel ausschütten (handgerechnet), und dass ein ausschüttendes
Instrument ohne eigenen Satz weiterhin auf den Platzhalter fällt. Für die TER
(#76): handgerechneter Abzug gegen `(1 − TER/52)^52` über 52 Wochen,
`fondskosten=False` reproduziert exakt den Wert ohne Kosten, Instrumente ohne
TER (Einzelaktien, 4GLD, BTC) bleiben unberührt, und ein teurer Fonds wird
stärker belastet als ein günstiger. Die TER-Tests nutzen bewusst
**thesaurierende** ETFs (EXXY/IBCI), damit die Jahresausschüttung den Endwert
nicht überlagert; bestehende handgerechnete Tests mit ETFs setzen
`fondskosten=False`.
`tests/test_learnings.py` fährt jede Learning-Regel gegen
konstruierte Views mit bekannten Zahlen und prüft, dass die Aussagen den
Daten folgen statt fest zu sein (inkl. Gegenprobe mit umgedrehter
Rangfolge) sowie dass nicht beantwortbare Regeln still wegfallen.
`tests/test_satellit_strategy.py` prüft den Einzelaktien-Satellit
(`BARBELL_20_60_20_SATELLIT`): Symbol-Mapping-Vollständigkeit, Ziel-Gewichte
summieren zu 1, 80/20-Risikoprofil bleibt erhalten, End-to-End-Smoke-Test
mit allen 17 Instrumenten. `tests/test_backfill_history.py` prüft
`scripts/backfill_history.py` (USD/EUR-Umrechnung inkl. Forward-Fill,
ISO-Wochen-Gruppierung, Carry-Forward fehlender Ticker) gegen ein
Fake-`AlphaVantageSource`-Objekt; seit #62 zusätzlich, dass
`_nearest_fx_rate()` **nicht** rückwärts extrapoliert (Datum vor Beginn der
FX-Reihe → `None`, Forward-Fill danach unverändert) und dass
`collect_weekly_series()` die betroffenen Wochen der USD-Ticker und von
BTC-EUR fallen lässt statt sie falsch umzurechnen. Für die handgepflegten
Ergänzungen (#62): Einlesen beider Dateien inkl. `#`-Kommentarzeilen und
fehlender Datei, Abbruch bei unbekanntem Ticker, dass ein manueller
FX-Kurs eine Woche vor Beginn der FX-Reihe für USD-Ticker *und* BTC
umrechenbar macht, dass `manual_prices`-Werte **nicht** noch einmal
umgerechnet werden, dass sie einen API-Wert derselben ISO-Woche ersetzen
statt danebenzustehen, dass Ticker außerhalb der angefragten Liste
ignoriert werden — und End-to-End, dass ein handgepflegter Kurs den
kompletten Neuaufbau von `price_history.csv` übersteht. Für die Splitbereinigung
(#62) in `tests/test_alphavantage.py`: dass `fetch_weekly_history()` den
`TIME_SERIES_WEEKLY_ADJUSTED`-Endpunkt anfragt, dass eine nachgebaute
TSLA-Reihe (5:1 und 3:1) ohne Phantom-Absturz herauskommt, dass ein
dividendengroßer Faktorsprung eben **nicht** als Split gewertet wird (sonst
zählte die separat modellierte Dividende doppelt), und dass ein Symbol ohne
`adjusted close` unverändert beim Nominalkurs bleibt. Für den Erstkauf ohne
Rebalancing (#62) in `tests/test_engine.py`: eine Drei-Instrumente-Strategie
mit ungleichen Zielgewichten (40/40/20), bei der das dritte Instrument erst
später an den Markt kommt — mit `rebalancing=False` bleibt das
Wertverhältnis des Altbestands (2:1) exakt erhalten und nur das neue
Instrument wird auf sein Zielgewicht gekauft, mit `rebalancing=True` landen
weiterhin alle drei auf ihren Zielgewichten. `tests/test_neue_strategien.py`
prüft die beiden #64-Strategien: Sub-Gewichte je Topf summieren zu 1,
`BARBELL_20_80_DIVERSIFIZIERT` hält das 20/80-Risikoprofil und einen echten
Cash-Baustein statt eines Aktien-ETF in Topf A, `SP500_BENCHMARK` hält
ausschließlich `IUSA` und rebalanciert nie, beide laufen End-to-End durch —
sowie einen Regressionstest, dass `IBCI`/`EXXY` tatsächlich thesaurierend
statt ausschüttend sind (die im Zuge von #64 korrigierten Steuerattribute).
`tests/test_backfill_history.py::test_neue_datenreihen_bleiben_ausserhalb_
der_urspruenglichen_strategien` (vormals „…liegen_in_keinem_topf", seit die
sieben Instrumente alloziert sind umbenannt) sichert ab, dass die drei
ursprünglichen Barbell-Strategien und alle Szenarien weiterhin unberührt
bleiben — nur die beiden neuen Strategien allokieren die #64-Instrumente.
Für den Benchmark-Overlay-Schalter (#72) in `tests/test_dashboard.py`:
`_benchmark_reihen()` gegen eine (per `monkeypatch` auf
`dashboard.BENCHMARK_STRATEGIEN` gesetzte) Fixture-Benchmark-Strategie -
simuliert mit dem Startkapital der angezeigten (nicht der Benchmark-)
Strategie, schließt eine Benchmark-Strategie aus, die namensgleich mit der
angezeigten Strategie ist, und bietet einen Kandidaten ohne Kursdaten im
Zeitraum gar nicht erst an. End-to-End über `build_dashboard()`: der
Schalter (`id="benchmark-switch"`) wird auf Start- **und** Detailseite
gerendert, und die betroffenen Chart.js-Konfigurationen enthalten
nachweislich kein `suggestedMax:` mehr (nur noch das feste `max:`).
