# Security Audit (Issue #43)

Scope: this repo's own code, GitHub Actions workflows, and dependencies. No
user accounts, no server backend, no secrets beyond the one Alpha Vantage API
key — the attack surface is small by design (static site, deterministic pure
simulation, CSV as the only persistent state).

## Findings and fixes

| # | Finding | Risk | Fix |
|---|---|---|---|
| 1 | `ci.yml` had no explicit `permissions:` block, so `GITHUB_TOKEN` gets the repo's default (potentially write) permissions for a workflow that only runs tests | Low – unnecessary token privilege on every push/PR, including from forks | Added `permissions: contents: read` |
| 2 | `requirements.txt` pinned `yfinance` to a version range (`>=0.2.55,<0.3`) instead of an exact version | Low/medium – a new release in that range is installed automatically without review, unlike every other dependency in the file | Pinned to `yfinance==0.2.55`, matching the version already in use |
| 3 | No `.gitignore` entry for local `.env` files, even though the README/CLAUDE.md describe setting `ALPHAVANTAGE_API_KEY` locally | Low – accidental commit of a local secret file | Added `.env` / `*.env` to `.gitignore` |
| 4 | `templates/base.html.j2` had no `<meta name="description">`/Open Graph tags (see SEO section of #43) and loaded Chart.js from a CDN without any integrity/CORS attribute | Low – the missing `crossorigin` attribute means a CDN compromise of Chart.js would run unchecked in visitors' browsers | Added `crossorigin="anonymous"` to the `<script>` tag. **Not fixed:** a proper `integrity="sha384-…"` (Subresource Integrity) hash — this sandbox has no network egress to `cdn.jsdelivr.net`, and pinning a guessed/wrong hash would make the browser reject the script entirely, breaking every chart on the site. Computing and adding the real SRI hash for the pinned Chart.js version is a follow-up (`shasum -a 384` on the fetched file, or jsdelivr's own SRI hash listing) |

## Reviewed, no change needed

- **Templating/XSS:** `dashboard.py` builds the Jinja2 `Environment` with
  `autoescape=select_autoescape(["html"])` (`src/boersenspiel/dashboard.py:219`),
  so all rendered values are HTML-escaped by default. There is no user input
  in the render path anyway — all data comes from `data/price_history.csv`
  and the strategy/scenario definitions in the repo.
- **Command/code injection:** no `shell=True`, `eval`, `exec`, `pickle`, or
  `os.system` anywhere in `src/` or `scripts/`. All HTTP calls go through
  `requests` with a `params=` dict (no string-built URLs), and all subprocess
  use is limited to the workflow YAML's own shell steps (no user-controlled
  input reaches them — `inputs.years` and `inputs.confirm` in
  `backfill.yml` are operator-supplied via `workflow_dispatch`, not
  attacker-controlled, and are only compared/passed as CLI args, never
  interpolated into a shell string with untrusted content).
- **Secret handling:** the only secret, `ALPHAVANTAGE_API_KEY`, is read from
  `os.environ` (`src/boersenspiel/sources/alphavantage.py:75`), passed only
  as a request query parameter over HTTPS, and never logged or printed.
  Workflow files only reference it via `${{ secrets.ALPHAVANTAGE_API_KEY }}`
  in an `env:` block, the standard safe pattern.
- **Workflow permissions:** `weekly-update.yml` and `backfill.yml` grant
  `contents: write` at the workflow level for the job that commits price
  data, but the `deploy` job declares its own `permissions:` block
  (`pages: write`, `id-token: write`) — GitHub Actions job-level permissions
  *replace* the workflow-level ones for that job rather than adding to them,
  so `deploy` does not inherit `contents: write`. Already least-privilege.
- **Third-party GitHub Actions:** `actions/checkout`, `actions/setup-python`,
  `actions/upload-pages-artifact`, `actions/deploy-pages` are all
  GitHub-maintained actions referenced by major version tag (`@v4`/`@v5`).
  Pinning to a commit SHA is stricter but was left as-is here to avoid
  introducing a stale/wrong SHA without network access to verify it in this
  environment — worth doing in a follow-up with `gh` or the GitHub UI.
- **Data trust boundary:** `data/price_history.csv` is only ever written by
  `history_store.record_week()`, which is only called from the fetch/backfill
  scripts running in CI with a trusted price source. `engine.simulate()`
  treats it as trusted input and does not need to defend against adversarial
  CSV content.

## Out of scope

- Dependency vulnerability scanning (e.g. `pip-audit`/Dependabot alerts)
  requires either network access or a registered GitHub App this environment
  doesn't have; enabling Dependabot security updates in repo settings is a
  one-click follow-up.
