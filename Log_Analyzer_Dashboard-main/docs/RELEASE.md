# Release setup and validation

Phase 6 implements private packaging and repeatable engineering checks. It does **not** declare public release permission. The matched upstream content has no discovered license or supplied written permission; see [ATTRIBUTION.md](ATTRIBUTION.md). Nothing has been published or deployed. The GitHub Actions workflow is configured but has not run on a hosting provider.

## Supported setup

The validated local platform is Windows 11 with CPython 3.12.4. Metadata supports Python 3.12 only (`>=3.12,<3.13`). Ubuntu with Python 3.12 is a configured CI target, not a locally verified platform. Other Python minors/platforms require their own dependency and behavior checks before expanding the support claim.

Run from the nested application directory. Create a **new** environment; the older `.venv` used in previous phases inherited system packages and is not the release-validation environment. On this deeply nested Windows path, a shorter workspace-root environment avoids third-party path-length failures:

```powershell
python -m venv ..\.r
New-Item -ItemType Directory -Force ..\.t | Out-Null
$env:TEMP = (Resolve-Path ..\.t).Path
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$releasePython = (Resolve-Path ..\.r\Scripts\python.exe).Path
& $releasePython -m pip install -r requirements-dev.txt
& $releasePython -m pip check
& $releasePython -m pytest
```

Do not create this environment over an existing nonempty environment when claiming a fresh install. Use another short directory for an independent repeat. The project has no runtime dependency on the original inherited environment.

`requirements.txt` pins Streamlit 1.66.0 and Plotly 7.1.0. `requirements-dev.txt` adds exact installer, backend, build, test, coverage, and advisory-audit versions. Both apply `requirements/constraints-py312.txt`, the full 68-distribution constraint set resolved in the clean environment. Runtime installation does not install the dev-only packages merely because their versions appear in constraints.

These are exact **version constraints**, not a hash-locked or vendored dependency supply chain. Index availability and compatible platform wheels are still required. The project does not promise indefinite reproducibility from an unavailable package index. For an offline check on the same Python/platform, download the constrained wheels first and install using `--no-index --find-links`.

The build backend is setuptools 83.0.0 with wheel 0.48.0. The first advisory audit identified a setuptools 82.0.1 issue; the backend and constraints were upgraded to the reported fixed version before the successful audit. No finding was ignored to obtain a passing result.

## Build and installed-wheel verification

```powershell
& $releasePython -m build --no-isolation --outdir .runtime-tmp/dist
& $releasePython -m pip install --no-deps .runtime-tmp/dist/log_analyzer_local-0.6.0-py3-none-any.whl
& $releasePython scripts/check_installed.py
```

`--no-isolation` uses the already constrained build tools. The default build produces a source archive and then a wheel from that archive. Use a short temporary directory on Windows to avoid nested build paths exceeding OS limits. Build output remains private local validation material; no upload command or publishing workflow is configured.

The installed check verifies that imports come from site-packages rather than the source/editable tree, that bundled samples exist, that the installed `log-analyzer` command works from a temporary working directory with 10,048 events/543 errors/1,487 warnings, and that default output files go to that working directory. Streamlit AppTest executes the installed dashboard and verifies its 48 default events. A temporary server starts with the same command used by the launcher, binds to loopback, and passes its health endpoint check before cleanup. The health probe proves server startup; AppTest supplies application behavior checks. Browser-native file dialogs and manual visual review are not claimed.

The second validation environment (`..\.w`) was installed offline from downloaded runtime wheels plus the private project wheel. It has no pytest, build, setuptools, pip-audit, or coverage dependency. This checks that installed runtime behavior does not rely on the development environment. Its exact inventory is saved alongside the development inventory.

The install adds:

```powershell
log-analyzer --help
log-analyzer-dashboard --port 8501
```

The launcher runs locally on `127.0.0.1`, sets a 50 MiB framework upload limit, and disables Streamlit usage telemetry. Source checkout commands (`python main.py`, `python -m streamlit run dashboard.py`) remain available. Installed wheels read sample package data and use working-directory outputs; source checkouts retain application-relative defaults.

## Documentation walkthrough

Use synthetic fixtures and separate destinations:

```powershell
& $releasePython main.py tests/fixtures/events.jsonl --format jsonl --naive-policy reject --export json --report-dir .runtime-tmp/walkthrough-json --database .runtime-tmp/walkthrough-json.db
& $releasePython main.py tests/fixtures/investigation.log --bucket-seconds 60 --min-errors 3 --error-rate 0.5 --fail-on-alert --export json --report-dir .runtime-tmp/walkthrough-alert --database .runtime-tmp/walkthrough-alert.db
```

Expected exits are 0 and 3 respectively. Exit 3 is the requested diagnostic signal after successful output, not an import failure. Use `--start`, `--end`, `--severity`, and `--contains` as documented in the README; invalid settings fail before writes. No shipped logs/reports need to be regenerated. Generate optional seeded examples to a separate path using `generate_logs(output_path, seed=...)`.

Input encoding is UTF-8/UTF-8-BOM, outputs are UTF-8, timestamps normalize to UTC, and CSV prefix escaping is reversible as described in [INVESTIGATION.md](INVESTIGATION.md). Resource limits, source-quality precedence, persisted history growth, and per-file export atomicity are documented in [STORAGE.md](STORAGE.md). Diagnostics do not establish root cause, health, or statistical anomaly status.

## Regression, coverage, and advisory checks

```powershell
& $releasePython -m pytest --cov --cov-branch --cov-report=term-missing --cov-report=json:.runtime-tmp/coverage.json --junitxml=.runtime-tmp/tests.xml
& $releasePython -m pip_audit --disable-pip --no-deps --strict -r requirements/constraints-py312.txt --format json --output .runtime-tmp/audit.json --cache-dir .runtime-tmp/audit-cache
& $releasePython scripts/release_inventory.py --output .runtime-tmp/environment.json
```

The audit requests advisory data for the exact third-party constraints, including development/build tools. It does not audit local application source or establish release rights. A zero-finding result is time-specific and limited to that advisory database; it is not a security certification. Audit failures remain failures in CI. No blanket vulnerability exclusions are used.

Coverage measures the configured application modules with branch collection in the test process and covered AppTest threads. It does not imply complete browser interaction, all subprocess execution, or absence of defects. No percentage threshold was chosen merely to manufacture a passing badge. Actual totals and selected per-file results are saved in `PHASE6_VALIDATION.json` after validation.

Modern Streamlit's AppTest creates a local asyncio socket pair on Windows. In the restricted coding sandbox this stalls before the test-runner timeout unless loopback access is allowed. Final tests and installed-server checks were run with the required local access. The earlier sandbox-stalled checks were interrupted and are not counted as successful runs. This environment condition is separate from application behavior.

## Benchmark reproduction

```powershell
& $releasePython scripts/benchmark_storage.py --directory .runtime-tmp/benchmark-a --counts 2000 20000
& $releasePython scripts/benchmark_storage.py --directory .runtime-tmp/benchmark-b --counts 2000 20000
```

Use fresh directories. Existing source fixtures/databases are rejected without overwriting them. Inputs are deterministic, with distinct preserved error codes/signatures and labeled request IDs. Each result records source SHA-256, accepted/error/group counts, limits/options, byte sizes, elapsed time, peak traced Python allocations, query pages and insertion batches. Repeated runs must reproduce input hashes and semantic counts; timings, native allocation patterns and database page layouts are not promised byte-identical.

`PHASE6_BENCHMARK_A.json` and `PHASE6_BENCHMARK_B.json` record two local runs. As in Phase 4, the measured region covers import, SQL selection/grouping and bounded first pages. It excludes fixture creation/schema initialization, full exports, diagnostics and browser execution. Native SQLite/OS/browser RSS is not measured. No speedup relative to prior phases or constant-total-memory claim is made.

## CI location and remaining release gates

The prepared workflow is at the **outer workspace root**: `../.github/workflows/release-validation.yml`. It detects whether the application is at checkout root or in `Log_Analyzer_Dashboard-main/`. GitHub only discovers workflows under the repository root's `.github/workflows`; if the nested application becomes the Git repository root instead, move the workflow there before hosting. No Git metadata exists locally to settle that hosting layout automatically.

The workflow tests Python 3.12 on Windows and Ubuntu, installs constrained dependencies, checks dependency consistency, runs regression/dashboard/coverage checks, builds private artifacts, verifies the installed wheel/server, audits advisories, and records a benchmark/inventory. It has read-only repository permissions, no credential persistence or publish/deploy job, and uploads only specified validation reports for seven days. Official action releases are pinned to inspected commit IDs recorded in `CI_ACTIONS.json`. A prepared workflow and YAML parsing do not prove a hosted run passed.

Release acceptance remains pending until the upstream license/written permission is established for the intended publication/redistribution and the workflow actually passes on the chosen hosting provider. This implementation cannot invent either result. Architecture, claims and inherited/original work are documented; there is no new license or public release declaration.
