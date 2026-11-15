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

**README.md richtet sich an Nutzer** (Zweck, Dashboard-Link, Hinweise; project-templates#156) —
keine Entwickler-Details. Die frühere ausführliche technische Fassung (Architektur-Diagramm,
Engine-Modellierungsentscheidungen, Steuerlogik, Szenario-Tabellen, Portfolio overview,
bekannte Einschränkungen) liegt unverändert in
[`docs/TECHNICAL_REFERENCE.md`](docs/TECHNICAL_REFERENCE.md). Code-Kommentare mit
„siehe README" meinen diese Datei.

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

<!-- GLOBAL POLICY:START -->
## [GLOBAL POLICY]

> Automatisch synchronisiert aus project-templates (Issue #7). Nicht manuell editieren –
> Änderungen hier werden beim nächsten Sync überschrieben. Quelle anpassen statt lokal.

- PRs immer gegen `testing`, nie direkt gegen `staging` oder `main`
- Merge auf `main` nur mit expliziter schriftlicher Freigabe
- `--delete-branch` nur für Feature-Branches (nie staging/testing)
- **Lokales Branch-Cleanup:** `main` und `testing` NIE löschen — auch nicht beim Bulk-Delete verwaister `[gone]`-Branches. Ein fehlender `origin/main`/`origin/testing` ist ein **wiederherzustellender Defekt** (lokal behalten, nach origin zurückpushen), kein Aufräum-Signal.
- `--no-verify` nur auf explizite Bitte
- **Vor jedem Push: lokale Tests ausführen** (`npm test` bzw. projektspezifischer Test-Befehl) – kein Push ohne grüne lokale Tests
- **Kein Merge bei CI-Fail** – Branch Protection erzwingt das technisch; nie mit `--admin` umgehen außer auf explizite Bitte
- **Zugehöriges Issue beim Merge schließen** (Issue #111): `Closes #X` im PR-Body greift nur beim Merge in den Default-Branch (`main`) — bei PRs nach `testing` also **nie**. Das Issue nach dem Merge manuell schließen (`gh issue close <N> -c "Umgesetzt in #<PR>, gemergt nach \`testing\`."`), sonst bleiben erledigte Issues offen liegen. Ausnahme: Sammel-/Meta-Issues, die ein Teil-PR nur anteilig abarbeitet — die bleiben offen. `Closes #X` trotzdem im PR-Body lassen: es erzeugt die sichtbare Verknüpfung.
- **Vor dem Start an einem offenen Issue** (Issue #152): prüfen, ob ein Branch/PR `feature/issue-<N>-*` existiert und bereits in `testing` gemergt ist (`gh pr list --state all --search "issue-<N>"`). Offene Checkboxen ≠ offene Arbeit — sonst wird Erledigtes doppelt umgesetzt.

## [ANDROID BUILD – PFLICHTREGELN]

- **Git-Tag** nach jedem Play-Store-Upload setzen: `git tag vX.Y.Z && git push origin vX.Y.Z` – der Tag markiert den tatsächlich veröffentlichten Stand und dient als Changelog-Baseline für den nächsten Build
- **EAS Local Build (DrawFromMemory):** Workingdir vor jedem Build leeren: `rm -rf ~/tmp/eas-build && mkdir -p ~/tmp/eas-build` – ein nicht-leeres Verzeichnis bricht den Build sofort ab
- **Disk-Check vor EAS Build:** Skia-Libraries benötigen ~5–8 GB. Bei < 5 GB frei: `npm cache clean --force && rm -rf ~/.npm/_npx` (~13 GB, sicher löschbar)
- **JAVA_HOME** für EAS/Expo-Builds explizit auf Android Studio JBR setzen: `export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`
- **Gradle-Lock nach Absturz:** Bei "Cannot lock file hash cache"-Fehler Daemons stoppen: `pkill -f GradleDaemon`, dann Workingdir leeren und neu starten
- **AAB-Archiv:** Gebaute Release-AABs in einem **gitignored** `aab-archive/`-Verzeichnis im Repo-Root ablegen (in `.gitignore` aufnehmen – AABs sind 3–110 MB und gehören nie in die Git-History). Benennung: `<Projekt>-vX.Y.Z-vc<versionCode>-YYYY-MM-DD.aab`. **Retention: max. 2 Dateien** (aktuelles Release + ein Vorgänger für schnelles Rollback); ältere AABs löschen. Der Git-Tag `vX.Y.Z` ist die eigentliche Release-Baseline – ältere AABs lassen sich daraus jederzeit neu bauen.

## [CLAUDE.MD-WARTUNG]

- **CLAUDE.md bleibt bei maximal 300 Zeilen** (Issue #160): Sie wird bei jeder Session vollständig in den Kontext geladen. Beschreibt ein Abschnitt einen konkreten Vorfall, gehören maximal 2-3 Zeilen (Kernregel + kurzer Auslöser-Kontext) + ein Link auf `docs/private/INCIDENTS.md` hinein; aktuell gültiges Architektur-/Prozesswissen, das kein Vorfall ist, aber zu ausführlich für CLAUDE.md, gehört in versionierte `docs/*.md`-Dateien (z. B. `docs/ARCHITECTURE.md`). Die Schwelle ist ein Prüf-Auslöser, kein Zwang, bewusst dort gehaltenes, aktuelles Architekturwissen aus CLAUDE.md zu verdrängen. Aktiv gekürzt wird erst ab 500 Zeilen; Dateien zwischen 300 und 500 Zeilen werden im Turnus nicht angefasst. Ausführlicher Prozess, Checkliste und Stand pro Projekt: https://github.com/S540d/project-templates/blob/main/dev-standards/claude-md-maintenance.md
- **`docs/private/INCIDENTS.md` ist bewusst gitignored** — reine lokale Gedächtnisstütze wie Memory, kein Teil des geteilten Repo-Zustands. In jedem Projekt mit dieser Datei muss `.gitignore` einen Eintrag `docs/private/` enthalten; existiert die Datei bereits versioniert (z. B. als `docs/INCIDENTS.md`), gehört sie nach `docs/private/` verschoben und per `git rm --cached` aus dem Tracking genommen.
- **Regelmäßig `/simplify` auf CLAUDE.md ausführen**, nicht nur einmalig beim Überschreiten der Schwelle — Ziel ist dauerhaft niedriger Token-Verbrauch pro Session statt zyklischem Anwachsen und Zurückkürzen in großen Sprüngen.

## [CODE HEALTH AUDIT]

- **Wiederkehrendes Code-Health-Audit** (Ballast/Architektur: God Components, Boilerplate-Duplikation, toter Code, Dependency-Bloat, Test-Integrität, Design-Konsistenz, Bundle-Größe) alle ~3 Monate oder ~15 gemergte Feature-PRs (je nachdem was zuerst eintritt). Checkliste + Ablauf: https://github.com/S540d/project-templates/blob/main/dev-standards/code-health-audit.md — Ergebnis ist immer ein Issue im jeweiligen Projekt-Repo, nie in project-templates.

## [SIMPLIFY-AUDIT]

- **Wiederkehrender `/simplify`-Durchlauf auf den Quellcode** (Reuse, Simplification, Efficiency, Altitude) alle ~3 Monate oder ~15 gemergte Feature-PRs (je nachdem was zuerst eintritt), gleiche Kadenz wie das Code-Health-Audit. Anders als dieses wendet er die Fixes direkt an: Ergebnis ist ein PR gegen den projektüblichen Ziel-Branch, nur kleine, verhaltensneutrale Refactorings (bei Unsicherheit Finding auslassen). Ablauf: https://github.com/S540d/project-templates/blob/main/dev-standards/simplify-audit.md

## [ÜBER-ABSCHNITT]

- **Einheitlicher „Über"-Abschnitt im Settingsmenü** (Issue #150): Jedes Web-Projekt zeigt „Über" als Eintrag in einem `⋮`-Settingsmenü (kein Footer — wird bei Bedarf neu angelegt, auch für aktuell menülose Projekte). Fester Vollausbau: App-Name, Version, Impressum, Datenschutz, Quellcode, Play Store, Feedback — nicht zutreffende Felder werden weggelassen, nie umsortiert. Spezifikation: https://github.com/S540d/project-templates/blob/main/dev-standards/about-section.md — Umsetzung ist immer ein Issue im jeweiligen Projekt-Repo, nie in project-templates.

## [CI – CACHE-CLEANUP]

- **Cache-Cleanup-Workflow** (`.github/workflows/cache-cleanup.yml`) in jedem Repo mit GitHub-Actions-Caches: löscht wöchentlich (So 03:00 UTC) bzw. on-demand alle Action-Caches älter als der jeweils letzte Lauf. GitHub-Limit ist 10 GB pro Repo – ohne Cleanup laufen Build-Caches (node_modules, Gradle, Expo) voll und verdrängen frische Einträge. Vorlage: `cache-cleanup.yml` in project-templates.

## [COMMUNITY-STANDARDS]

- **Einheitliche Community-Dateien** (Issue #196): `CODE_OF_CONDUCT.md` und `CONTRIBUTING.md` liegen **zentral** in `S540d/.github` und gelten für alle Repos ohne eigene Datei — keine projekteigenen Kopien anlegen. `SECURITY.md` (Vorlage `automation-templates/SECURITY.md`) und `LICENSE` liegen je Repo. Lizenz-Standard ist **PolyForm Noncommercial 1.0.0** (`package.json`: `PolyForm-Noncommercial-1.0.0`); Drittinhalte mit eigener Lizenz separat ausweisen.
- **Keine Issue-/PR-Templates** (`.github/ISSUE_TEMPLATE/`, `PULL_REQUEST_TEMPLATE*`): werden nicht genutzt und nicht angelegt. Checklisten gehören nach `docs/`, nicht nach `.github/ISSUE_TEMPLATE/`.
- **Repo-Description** (GitHub-Einstellung) ist gesetzt und enthält **nur funktionale Hinweise für Anwender** (eine Zeile, kein Tech-Stack, keine Setup-/Clone-Hinweise).
<!-- GLOBAL POLICY:END -->
