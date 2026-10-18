# Log Analyzer Dashboard

A local Python 3.12 log investigation application with a Streamlit dashboard, Plotly charts, SQLite storage, and a command-line workflow. It supports text/JSON Lines parsing, UTC filters, recurring-error groups, diagnostics, and text/CSV/JSON exports.

The application is in [`Log_Analyzer_Dashboard-main/`](Log_Analyzer_Dashboard-main/). Start with its [README](Log_Analyzer_Dashboard-main/README.md) for features, installation, configuration, examples, tests, and limitations.

```powershell
cd Log_Analyzer_Dashboard-main
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run dashboard.py --server.address 127.0.0.1
```

The repository-level `.github/workflows/` directory contains validation configuration. Local environments, generated outputs, and secrets are excluded by `.gitignore`.

## Attribution

The inherited project credits **Swarananjali** and **Nandini**. Local extensions were developed with coding-agent assistance. Preserve the [attribution](Log_Analyzer_Dashboard-main/docs/ATTRIBUTION.md) and [upstream provenance](Log_Analyzer_Dashboard-main/docs/UPSTREAM_PROVENANCE.json).

