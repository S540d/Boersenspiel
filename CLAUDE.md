# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Virtuelles Portfolio-Dashboard nach einer Barbell-Strategie, basierend auf
einem ursprünglichen Anforderungsdokument aus der frühen Planungsphase
(nicht Teil dieses Repos). Wöchentlicher Kursabruf via GitHub Actions, Kurshistorie als CSV im Repo,
statisches Dashboard (Chart.js) auf GitHub Pages. Default-Branch ist `main`
(ursprünglich hieß er `claude/pflichtenheft-umsetzung-planen-6kf05s`, da das
Repo leer angelegt wurde, und wurde nachträglich zu `main` umbenannt).

## Commands

```bash
pip install -r requirements.txt

pytest -q                          # gesamte Testsuite
pytest tests/test_engine.py -q     # einzelne Testdatei
pytest tests/test_engine.py::test_simple_strategy_end_to_end_exact_values -q  # einzelner Test

python scripts/run_fetch.py                        # Kursabruf via Alpha Vantage (benötigt ALPHAVANTAGE_API_KEY env var)
python scripts/record_prices.py --date 2026-08-17 --prices '{"EUNL": 82.1, ...}'  # manueller Kurseintrag
python scripts/backfill_history.py --years 20       # einmaliger historischer Backfill (ersetzt price_history.csv, ~18 API-Requests)
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

- `instruments.py` — die 17 Instrumente (7 Barbell-Basisinstrumente + 10
  Einzelaktien-Satellit, s.u.), **quellenunabhängig**. Kein
  Provider-Symbol-Mapping hier.
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
  `Strategy.optimierungen` (Instanz von `Optimierungen`, vier Bool-Schalter
  `steueroptimierung`/`rebalancing`/`ordergebuehren`/`besteuerung`, alle
  standardmäßig `True`) bestimmt, welche der vier strategieübergreifenden
  Simulationsmechanismen `engine.simulate()` für diese Strategie anwendet —
  `BUY_AND_HOLD` nutzt z. B. `Optimierungen(rebalancing=False)` statt einer
  künstlich unerreichbaren Rebalancing-Schwelle.
- `scenarios.py` — Auswertungs-Szenarien als gewöhnliche `Strategy`-Instanzen
  mit gesetztem `gewichte_fn`, in drei Kategorien: (1) Börsenweisheiten —
  "Sell in May and Go Away" (saisonal defensiv Mai–September), "Buy & Hold"
  (nie aktiv rebalancieren), "Jahresendrallye" (Dez/Jan Wachstumsquote auf
  95%), "Antizyklisch kaufen" (Wachstumsquote auf 95% nach >10% Rückgang vom
  Rolling-Hoch) und "Verluste begrenzen" (Trailing-Stop je Wachstums-
  Instrument, >15% Rückgang vom eigenen Rolling-Hoch schaltet nur dieses
  Instrument auf 0%) sowie "Börsenweisheiten (alle fünf kombiniert)", das die
  fünf zu einer Strategie zusammenfasst: jede Weisheit ist ein `Weisheit`-
  Baustein, der in Phase 1 eine Wachstumsquote votiert (oder sich enthält,
  wenn seine Bedingung diese Woche nicht greift) und/oder in Phase 2 als
  Instrument-Overlay wirkt. Ziel-Quote ist das **arithmetische Mittel der
  abgegebenen Voten** — widersprüchliche Signale (Mai-Ausstieg vs. Dip-Kauf)
  heben sich damit teilweise auf, statt dass eine Regel die anderen
  überstimmt; "Buy & Hold" votiert als einzige immer (für die normale Quote)
  und wirkt so als dämpfender Anker — dieser Mechanismus-Unterschied zum
  Solo-Szenario (dort schaltet "Buy & Hold" stattdessen komplett das
  Rebalancing ab) steht seit #27 auch als `beschreibung` auf der
  `BUY_AND_HOLD`-Detailseite, nachdem der Owner entschieden hat, dass die
  Mechanik selbst unverändert bleibt. "Verluste begrenzen" ist die einzige
  Overlay-Regel und läuft nach Phase 1. Der Einzeleffekt jedes Spruchs kommt
  über `Strategy.beitraege` (Leave-one-out) ins Dashboard; (2) Charttechnik — SMA-Crossover (Golden/Death Cross,
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
  Backfill einsortiert würde. `read_fetch_log()` liest `fetch_log.csv`
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
  `float`. Dezember-Harvest realisiert Verluste (größter zuerst) bis der
  verbleibende Sparerpauschbetrag des Jahres gedeckt ist, mit sofortigem
  Rückkauf zum selben Kurs. `simulate(price_history, strategy,
  optimierungen=None)` nimmt optional eine `Optimierungen`-Instanz entgegen
  (Default: `strategy.optimierungen`) und schaltet damit die vier
  strategieübergreifenden Mechanismen einzeln ab: `ordergebuehren=False`
  macht alle Trades gebührenfrei (lokale `gebuehr`-Variable statt der
  Konstante `ORDERGEBUEHR`), `besteuerung=False` lässt `process_realized_gain`
  früh zurückkehren (Freibetrag/Verlustvortrag/kumulierte Steuer bleiben
  unverändert — der simulierte Portfoliowert selbst wird nirgends um Steuer
  gemindert, das ist reines Tracking), `rebalancing=False` überspringt die
  periodische Rückführung auf die Zielgewichte, `steueroptimierung=False`
  überspringt den kompletten Dezember-Harvest-Block. Diese Schalter dienen
  dazu, den isolierten Renditebeitrag jedes Mechanismus messbar zu machen
  (#17) — siehe `dashboard._optimierungs_effekte()`.
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
- `dashboard.py` + `templates/` — reine Darstellungsschicht, rendert
  `engine.simulate()`-Ergebnisse für alle (oder eine ausgewählte)
  Strategie(n) aus `STRATEGIES`. Seit #31 zwei Seitentypen statt einer
  einzigen `index.html`: `templates/base.html.j2` definiert Kopf/Fuß/Styles
  einmal per Jinja-Vererbung (`{% extends %}` + Blocks `title`/
  `header_extra`/`content`/`scripts`); `templates/dashboard.html.j2` (die
  Startseite `docs/index.html`) zeigt die strategieübergreifende
  Vergleichsübersicht ("Übersicht: Rendite im Vergleich" - Balkendiagramm +
  nach Rendite sortierte Tabelle, Zeilen verlinken auf die Detailseite) sowie
  je Strategie nur Name, Kurzbeschreibung und den Wertverlauf-Chart (mit
  gemeinsamer Y-Achsen-Skalierung über alle Strategien hinweg, siehe unten);
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
  vier `Optimierungen`-Mechanismen (#17) als Leave-one-out-Differenz zur
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
  wenig Risiko. `_RISIKOFREIER_ZINS_PLATZHALTER = 0.0` ist ein bewusster
  Platzhalter nach demselben Muster wie
  `VORABPAUSCHALE_BASISZINS_PLATZHALTER`, kein echter Referenzzins. Jede
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

### Kursquelle wechseln

Jede Quelle liefert nur `dict[ticker, PriceQuote]` an
`history_store.record_week()` — Engine/Dashboard/Tests sind davon
unabhängig. Um z. B. auf manuellen Kursabruf (Websuche/Cowork) umzustellen,
statt `scripts/run_fetch.py` einfach `scripts/record_prices.py --date ...
--prices '{...}'` mit den ermittelten Kursen aufrufen; der
GitHub-Actions-Cron-Schritt kann dafür deaktiviert werden, ohne den Rest des
Systems anzufassen.

### Historischer Backfill

`data/price_history.csv` wächst im Normalbetrieb nur Woche für Woche seit
Projektstart, weil `GLOBAL_QUOTE` nur den aktuellen Kurs liefert.
`scripts/backfill_history.py` nutzt stattdessen `TIME_SERIES_WEEKLY` /
`DIGITAL_CURRENCY_WEEKLY` (komplette verfügbare Historie in **einem**
Request pro Ticker) und schreibt über `history_store.record_week()`
(derselbe Pfad wie der Live-Abruf) die komplette Historie neu. USD-Ticker
werden mit dem historischen `FX_WEEKLY`-Kurs derselben Woche umgerechnet
(Forward-Fill bei fehlender Woche). Reine Datenbeschaffung
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
nicht am selben Tag wie den wöchentlichen Kursabruf starten (18 + 18
Requests > Tageslimit 25). `--years` ist nur eine untere Schranke, die
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

Läuft wöchentlich (Montag 06:00 UTC) + `workflow_dispatch`: Tests →
Kursabruf (Alpha Vantage) → Dashboard-Build → Commit von
`data/price_history.csv`/`data/fetch_log.csv` zurück ins Repo →
GitHub-Pages-Deploy. Braucht die Secrets/Settings: Repo-Secret
`ALPHAVANTAGE_API_KEY`; Settings → Actions → Workflow permissions → "Read
and write permissions"; Settings → Pages → Source → "GitHub Actions".

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
steuerfrei, andernfalls wäre eine deutliche Steuer sichtbar).
`tests/test_history_store.py` prüft Wochen-Idempotenz, Carry-Forward und
`read_fetch_log()`.
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
gerendert. Seit #40/#41/#42 zusätzlich: Volatilität/Max-Drawdown-Funktionen
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
nur bei genug Kurshistorie erscheint.
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
Fake-`AlphaVantageSource`-Objekt.
