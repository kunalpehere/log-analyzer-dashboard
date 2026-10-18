# Log Analyzer Dashboard

A local Python application for investigating application logs through an interactive dashboard and command-line interface. It turns text or JSON Lines logs into searchable events, recurring-error groups, charts, and exportable evidence backed by SQLite.

Built with **Python 3.12, Streamlit, Plotly, and SQLite**. Automated checks use pytest and Streamlit AppTest.

## Features

- Parse text and JSON Lines logs, normalize severity and timestamps to UTC, and report malformed input.
- Filter by time, severity, and message text; inspect recurring ERROR/CRITICAL signatures and their source evidence.
- Browse persistent runs and source revisions through paginated queries.
- Visualize counts and timelines, with configurable error-count and error-rate diagnostic rules.
- Export investigations as text, CSV, and JSON using bounded processing and streamed output.
- Use the same investigation logic from the dashboard and CLI.

## Installation

Use CPython **3.12**; package metadata currently excludes other minor versions. Run these PowerShell commands from this application directory (the folder containing `pyproject.toml`):

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For development and tests, also install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Requirements use exact versions and `requirements/constraints-py312.txt`. This is a constraint set, not a hash-verified lockfile. No external service, API key, or separate database server is required. On Windows, use a shorter checkout/environment path if dependency installation encounters path-length errors. Fresh network installation has not been repeated during publication preparation.

## Run the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.address 127.0.0.1
```

Open the local URL printed by Streamlit. The dashboard reads three bundled sample logs or one uploaded file. Select the input format explicitly, then use its time, severity, text, and diagnostic controls. Uploads are limited to 50 MiB. `.streamlit/config.toml` disables usage telemetry.

## Run the CLI

Print the available options or analyze a synthetic fixture into separate generated outputs:

```powershell
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py tests/fixtures/events.jsonl --format jsonl --naive-policy reject --report-dir .runtime-tmp/demo --database .runtime-tmp/demo.db
```

For text logs, omit `--format jsonl`. Repeat `--severity ERROR`, `--severity CRITICAL`, or `--export json` as needed. Filters also include `--start`, `--end`, and `--contains`. Running `main.py` without arguments processes all four bundled logs and writes to the default `reports/` and `logs.db`; use explicit output paths to keep the inherited examples unchanged.

For optional local package installation and the `log-analyzer` / `log-analyzer-dashboard` entry points, see [release setup](docs/RELEASE.md). No package publication is configured.

## Configuration and input

Inputs must be UTF-8 (optionally BOM-prefixed). JSON Lines requires string `timestamp`, `level`, and `message` fields. Offset-free event timestamps default to UTC; use `--naive-policy reject` to require offsets. Filter timestamps always require an explicit offset. Resource limits and diagnostic thresholds are CLI arguments/dashboard controls, not secrets or environment variables.

CLI exit codes: **0** completed, **1** input/operational failure, **2** invalid arguments, **3** diagnostic alerts when `--fail-on-alert` is enabled. Errors appearing in a log do not by themselves cause a failed CLI run.

## Testing

A focused parser and analysis check:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_analysis.py
```

Run the full suite with `python -m pytest` using the development environment when needed. Tests use isolated temporary outputs and check that shipped examples remain unchanged. Historical validation records describe earlier local runs; see the [release documentation](docs/RELEASE.md). The repository workflow targets Windows and Linux but hosted execution remains unverified.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py`, `dashboard.py` | CLI and Streamlit entry points |
| `log_analyzer/` | Parsing, ingestion, filtering, grouping, storage, diagnostics, exports |
| `logs/`, `reports/` | Inherited sample inputs and reference reports |
| `tests/`, `tests/fixtures/` | Regression checks and synthetic fixtures |
| `requirements/`, `pyproject.toml` | Dependency constraints and local package configuration |
| `docs/`, `scripts/` | Technical documentation and validation utilities |

## Limitations

This is a local investigation tool. Diagnostic groups and alerts describe observations; they do not establish root cause or predict incidents. Default ingestion limits include 50 MiB per source and 250,000 event/issue outcomes. Retained database history grows on disk; automatic retention cleanup is not implemented. Browser downloads are limited to 5 MiB per artifact. Exports are atomic per file, not across an entire multi-file investigation. Windows/Python 3.12 is the documented local validation platform; other platforms need validation.

See the [detailed usage guide](docs/USAGE.md), [architecture](docs/ARCHITECTURE.md), [input formats](docs/FORMATS.md), [storage](docs/STORAGE.md), and [diagnostics](docs/DIAGNOSTICS.md) for exact behavior and limits.

## Attribution

The inherited project credits **Swarananjali** and **Nandini**. This local extension retains their original work and adds the parsing, investigation, storage, diagnostic, packaging, and testing changes recorded in [ATTRIBUTION.md](docs/ATTRIBUTION.md). Subsequent local changes used coding-agent assistance; this project is not presented as wholly original work.

The matching upstream is [anjali03102005/Log_Analyzer_Dashboard](https://github.com/anjali03102005/Log_Analyzer_Dashboard). [Provenance records](docs/UPSTREAM_PROVENANCE.json) document the inherited assets.
