"""Deterministic observations on immutable selections, with bounded Python state.

Rates describe the filtered evidence, never service health or proven root cause.
SQLite aggregates occupied buckets; missing buckets do not imply zero traffic.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json


@dataclass(frozen=True)
class DiagnosticOptions:
    bucket_seconds: int = 300
    min_errors: int = 5
    error_rate: str = '0.5'
    top_signatures: int = 10

    def __post_init__(self):
        if type(self.bucket_seconds) is not int or not 1 <= self.bucket_seconds <= 86400:
            raise ValueError('Diagnostic bucket seconds must be an integer from 1 to 86400')
        if type(self.min_errors) is not int or not 1 <= self.min_errors <= 2**31-1:
            raise ValueError('Diagnostic minimum errors must be a positive integer <= 2147483647')
        if type(self.top_signatures) is not int or not 1 <= self.top_signatures <= 100:
            raise ValueError('Top signatures must be an integer from 1 to 100')
        # Bound decimal precision/exponents before converting to an exact fraction.
        raw = str(self.error_rate)
        if len(raw) > 32:
            raise ValueError('Diagnostic error rate must be a short decimal from 0 to 1')
        try:
            rate = Decimal(raw)
            if not rate.is_finite() or not 0 <= rate <= 1 or not -18 <= rate.as_tuple().exponent <= 18:
                raise ValueError('Diagnostic error rate must be from 0 to 1 with at most 18 decimal places')
        except (InvalidOperation, TypeError) as error:
            raise ValueError('Diagnostic error rate must be a decimal from 0 to 1') from error
        object.__setattr__(self, 'error_rate', format(rate, 'f'))

    @property
    def cache_key(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


DEFAULT_DIAGNOSTICS = DiagnosticOptions()
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DENOMINATOR = 'All accepted logical events in this bucket after ALL investigation filters (including severity and message); rejected lines excluded'
HYPOTHESIS = 'A shared failure condition may explain repeated errors; counts alone cannot establish a cause.'
NEXT_STEPS = 'Inspect the referenced original events, source spans and tracebacks; compare surrounding evidence and service telemetry before assigning a cause.'


def _bucket_key(stamp, seconds):
    delta = datetime.fromisoformat(stamp) - EPOCH
    return (delta.days * 86400 + delta.seconds) // seconds


def _boundary(seconds):
    days, remainder = divmod(seconds, 86400)
    ordinal = EPOCH.toordinal() + days
    calendar = '0000-12-31' if ordinal == 0 else ('10000-01-01' if ordinal > date.max.toordinal() else date.fromordinal(ordinal).isoformat())
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    return f'{calendar}T{hours:02d}:{minutes:02d}:{secs:02d}+00:00'



def iter_buckets(view, options=DEFAULT_DIAGNOSTICS):
    """Stream occupied UTC buckets in chronological order, not a dense calendar."""
    fraction = Fraction(options.error_rate)
    with view.store.connection() as conn:
        conn.create_function('diagnostic_bucket', 1, lambda stamp: _bucket_key(stamp, options.bucket_seconds), deterministic=True)
        cursor = conn.execute("""SELECT diagnostic_bucket(e.ts_key) AS bucket, COUNT(*) AS denominator,
            SUM(e.level IN ('ERROR','CRITICAL')) AS errors,
            MIN(CASE WHEN e.level IN ('ERROR','CRITICAL') THEN s.position END) AS evidence_position
            FROM selected_events s JOIN stored_events e ON e.id=s.event_id
            WHERE s.selection_id=? GROUP BY bucket ORDER BY bucket""", (view.id,))
        while True:
            rows = cursor.fetchmany(view.store.limits.batch_size)
            if not rows:
                break
            for row in rows:
                count, total = row['errors'], row['denominator']
                triggered = count >= options.min_errors and count * fraction.denominator >= total * fraction.numerator
                yield {
                    'window_start_utc': _boundary(row['bucket'] * options.bucket_seconds),
                    'window_end_exclusive_utc': _boundary((row['bucket'] + 1) * options.bucket_seconds),
                    'errors': count, 'denominator': total, 'error_rate': count / total,
                    'denominator_definition': DENOMINATOR,
                    'minimum_errors': options.min_errors, 'error_rate_threshold': options.error_rate,
                    'alert': triggered,
                    'evidence_event_ids': [f"event-{row['evidence_position']:06d}"] if count else [],
                }


def iter_alerts(view, options=DEFAULT_DIAGNOSTICS):
    for bucket in iter_buckets(view, options):
        if bucket['alert']:
            yield dict(bucket, observation='Both configured error count and rate thresholds were met in the selected evidence.',
                       hypothesis=HYPOTHESIS, suggested_next_steps=NEXT_STEPS)


def ranked_signatures(view, options=DEFAULT_DIAGNOSTICS):
    # Existing SQL ranking is count desc, first occurrence, severity, signature, key.
    # Count-one signatures cannot precede any recurring signatures in this ordering.
    result = []
    from itertools import islice
    for group in islice(view.iter_groups(), options.top_signatures):
        if group['count'] < 2:
            break
        key = group.pop('group_key')
        result.append(dict(group, evidence_event_ids=[row['event_id'] for row in view.examples(key)],
                           observation='This normalized error signature occurs repeatedly in the selected evidence.',
                           hypothesis=HYPOTHESIS, suggested_next_steps=NEXT_STEPS))
    return result


def summary(view, options=DEFAULT_DIAGNOSTICS):
    occupied = alerts = 0
    for bucket in iter_buckets(view, options):
        occupied += 1
        alerts += int(bucket['alert'])
    return {
        'rule_version': 'utc-count-rate-v1', 'options': asdict(options),
        'selection_id': view.id, 'filters': view.filters.as_dict(),
        'bucket_origin_utc': EPOCH.isoformat(), 'boundaries': 'start inclusive, end exclusive',
        'denominator_definition': DENOMINATOR, 'error_levels': ['ERROR', 'CRITICAL'],
        'comparison': 'errors >= minimum_errors AND errors / denominator >= error_rate_threshold (exact decimal comparison)',
        'occupied_buckets': occupied, 'alert_count': alerts,
        'source_rejected_lines': sum(source.rejected_count for source in view.sources),
        'caveats': 'Filtered rates are conditional on the selection. Edge buckets may contain partial windows. Missing buckets mean no accepted selected evidence, not zero traffic. No alerts does not establish health. Rejected or missing logs can bias results.',
        'ranked_recurring_signatures': ranked_signatures(view, options),
    }


def assess_status(view, options=DEFAULT_DIAGNOSTICS, findings=None):
    """Keep source-quality precedence; replace legacy count-only urgency labels."""
    from status_policy import assess_investigation, Status
    quality = assess_investigation(view)
    if quality.label in {'Failed', 'Empty', 'Uninterpretable', 'Incomplete', 'No matches'}:
        return quality
    findings = summary(view, options) if findings is None else findings
    if findings['alert_count']:
        return Status('Rule alerts', f"{findings['alert_count']} UTC buckets meet both configured thresholds. Inspect the linked evidence; this does not prove a cause.", 'warning')
    return Status('No rule alerts', 'No selected UTC bucket meets both configured thresholds. This does not establish system health.', 'info')
