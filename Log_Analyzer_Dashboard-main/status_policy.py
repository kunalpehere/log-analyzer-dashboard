"""One evidence-based status policy for every presentation."""

from dataclasses import dataclass
from typing import Sequence

from log_analyzer.models import ScanResult, ScanState


@dataclass(frozen=True)
class Status:
    label: str
    message: str
    tone: str


def assess_results(results: Sequence[ScanResult]) -> Status:
    errors = sum(result.error_count for result in results)
    if any(result.state == ScanState.FAILED for result in results):
        return Status("Failed", f"At least one source could not be read. Observed errors in readable sources: {errors}.", "error")
    parsed = sum(result.parsed_count for result in results)
    rejected = sum(result.rejected_count for result in results)
    if not parsed:
        if rejected:
            return Status("Uninterpretable", f"No valid records; {rejected} nonblank lines rejected.", "warning")
        return Status("Empty", "No nonblank records available to analyze.", "warning")
    if rejected or any(result.state == ScanState.EMPTY for result in results):
        return Status("Incomplete", f"Some sources are empty or contain rejected input ({rejected} rejected lines). Observed errors: {errors}.", "warning")
    return _assess_counts(errors, sum(result.warning_count for result in results))


def _assess_counts(errors: int, warnings: int) -> Status:
    if errors > 10:
        return Status("Critical", f"{errors} error-level records observed. Inspect the evidence promptly.", "error")
    if errors > 5:
        return Status("Moderate", f"{errors} error-level records observed. Investigate soon.", "warning")
    if errors:
        return Status("Minor", f"{errors} error-level records observed. Inspect the evidence.", "warning")
    if warnings:
        return Status("Warnings", f"No error-level records observed; {warnings} warnings require review.", "warning")
    return Status("No errors observed", "No error-level records observed in the parsed input. This does not establish system health.", "success")


def assess_investigation(view) -> Status:
    """Preserve ingestion-quality failures without confusing an empty selection with empty input."""
    base = assess_results(view.sources)
    if not sum(result.parsed_count for result in view.sources):
        return base
    selected = view.event_count if hasattr(view, "event_count") else len(view.events)
    if base.label == "Failed":
        return Status("Failed", f"At least one source could not be read. Selected events: {selected}; selected errors: {view.error_count}.", "error")
    if base.label == "Incomplete":
        return Status("Incomplete", f"Some source input is empty or rejected. Selected events: {selected}; selected errors: {view.error_count}. See unfiltered ingestion quality.", "warning")
    if not selected:
        return Status("No matches", "No parsed events match the current filters. Source scans contain other events.", "warning")
    return _assess_counts(view.error_count, view.warning_count)
