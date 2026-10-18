# Attribution and provenance

The inherited README credits **Swarananjali** and **Nandini**. Those credits are preserved. This work does not claim the original dashboard, example catalog, logs, or reports as newly authored.

On 2026-10-15, a read-only upstream investigation found [anjali03102005/Log_Analyzer_Dashboard](https://github.com/anjali03102005/Log_Analyzer_Dashboard). At commit `d528d0a1b218a9f5cca99b480e9f741f6765a45b`, all four local `logs/*.log` and all four local `reports/*_report.txt` files exactly match the repository's Git blob hashes. Its README credits the same two authors. This establishes a matching upstream content source; without local Git history it does not prove how this checkout was obtained or who owns every contribution.

The upstream API returned no recognized license, and the commit's file tree contained no license/copying/copyright-named files. No separate permission document was supplied locally. Raw observations, commit identity, and per-file hashes are recorded in [UPSTREAM_PROVENANCE.json](UPSTREAM_PROVENANCE.json). Absence of discovered evidence is not a declaration about every possible permission arrangement.

No new license has been applied. Local setup metadata labels the distribution `Private :: Do Not Upload`; this is an intent marker, not an access-control mechanism. Wheels/source archives are for local validation. Publication, redistribution, and portfolio release permissions remain unresolved until the rights holder supplies an applicable license or written permission covering the intended use. No author has been contacted and nothing has been published by this implementation.

## Inherited and subsequent work

| Area | Attribution record |
| --- | --- |
| Original project concept, initial root modules, dashboard structure, message catalog, shipped logs/reports | Inherited; preserve Swarananjali/Nandini credits |
| Phase 1 | Parser/accounting/status fixes, guarded entry points/generator, transactional snapshot safety, initial automated tests |
| Phase 2 | Shared event/parser contracts, JSON Lines, UTC normalization, supported traceback assembly, evidence preservation |
| Phase 3 | Filters, conservative signatures, selected investigations, evidence-linked groups, reversible CSV and JSON/text exports |
| Phase 4 | Bounded streams/batches, resource limits, SQLite runs/revisions/migrations, rollback/reuse, SQL grouping/pagination, streamed exports and measured allocation limits |
| Phase 5 | Configurable diagnostic buckets/count/rate rules, tentative hypotheses, ranked recurring signatures, expanded CLI and rule-aware exports/status |
| Phase 6 | Private packaging, constrained clean setup, installed-wheel checks, CI configuration, dependency/coverage evidence, architecture/release/provenance documentation |

Subsequent changes were implemented in this local workspace with coding-agent assistance. The user's legal name or ownership claim has not been invented. The development plan records actual validation and known limits for each phase. This table is a change record, not a determination of copyright ownership or release rights.

Third-party libraries retain their own licenses and notices in their distributions. Dependency audit results concern known package advisories; they do not resolve this project's upstream permission question or certify the application as secure.
