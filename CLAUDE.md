# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Wartung

**Vorfälle gehören in `docs/private/INCIDENTS.md`, nicht ausführlich
hierher** (project-templates#160): ein Abschnitt, der einen konkreten
gefundenen-und-behobenen Bug beschreibt, bekommt hier maximal 2-3 Zeilen
Kernregel + kurzer Auslöser-Kontext und einen Link auf den passenden
Abschnitt in `docs/private/INCIDENTS.md` (bewusst gitignored, reine
lokale Gedächtnisstütze). Datum, betroffene Dateien, Diagnose-Schritte
und Beispielzahlen stehen ausschließlich dort. Aktuell gültiges
Architektur-/Modellierungswissen (warum eine Strategie so aufgebaut ist,
wie eine Steuerregel funktioniert) gehört ebenso wenig direkt hierher,
sondern nach [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — CLAUDE.md
bleibt auf Kernregeln, Setup und Verweise beschränkt. Schwellenwert für
"CLAUDE.md zu groß": > 300 Zeilen als Anlass für den nächsten
Cleanup-Pass, regelmäßig per `/simplify` wiederholt statt nur einmalig.

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

Vollständige Architektur-/Modellierungsdokumentation (Datenfluss, Modulaufteilung,
Steuerlogik, Dashboard-Rendering, Kursquellen, Tests) steht in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — ausgelagert nach project-templates#160,
da dieser Abschnitt allein den Großteil der Datei ausmachte und bei jeder Session
vollständig in den Kontext geladen wurde, ohne für die meisten Aufgaben gebraucht zu
werden. Beim Ändern von Engine/Dashboard/Strategien vorher dort nachlesen.
