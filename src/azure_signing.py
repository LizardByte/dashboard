"""Incrementally collect existing Artifact Signing platform metrics."""

import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from src import helpers
from src.logger import log

RESOURCE_ID_ENV = 'AZURE_SIGNING_RESOURCE_ID'
CACHE_PATH = ('azure', 'signing.json')
REFRESH_INTERVAL = timedelta(hours=3)
HISTORY_DAYS = 90
BACKFILL_DAYS = 7
SETTLE_DAYS = 2
NO_STRUCTURED_ERROR_DETAILS = 'Azure returned no structured error details.'
RESOURCE_ID_PATTERN = re.compile(
    r'/subscriptions/[\w-]+/resourceGroups/[\w.()-]+/'
    r'providers/Microsoft\.CodeSigning/codeSigningAccounts/[\w-]+', re.IGNORECASE,
)
METRIC_ERROR_CATEGORIES = (
    ('time interval', ('timegrain', 'time grain', 'time-grain', 'interval')),
    ('namespace', ('namespace', 'metric configuration')),
    ('metric name', ('metricnames', 'metric name', 'metric named')),
    ('aggregation', ('aggregation',)),
    ('time range', ('timespan', 'time span', 'starttime', 'endtime', 'start time', 'end time')),
)


def _load_cache(base_dir: str) -> dict:
    """Read cached metrics, or return an unconfigured state."""
    try:
        with open(os.path.join(base_dir, *CACHE_PATH)) as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('daily'), list):
            return data
    except (OSError, ValueError):
        pass
    return {'status': 'disabled', 'daily': []}


def _dates(start: datetime, end: datetime) -> list[str]:
    """Return UTC dates in a half-open interval (including a partial final day)."""
    count = math.ceil((end - start).total_seconds() / 86400)
    return [(start + timedelta(days=i)).date().isoformat() for i in range(count)]


def load_data(base_dir: str) -> dict:
    """Publish counts and reporting coverage, without Azure identifiers or cache state."""
    cache = _load_cache(base_dir)
    data = {key: cache[key] for key in ('status', 'daily', 'collected_at', 'attempted_at') if key in cache}
    if not cache.get('collected_at'):
        return data
    now = datetime.fromisoformat(cache['collected_at'])
    days = {p['date']: p['completed'] for p in cache['daily']}
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = midnight + timedelta(days=1)
    for name, start in (
        ('month_to_date', midnight.replace(day=1)),
        ('last_30_days', today_end - timedelta(days=30)),
    ):
        values = [days.get(date) for date in _dates(start, today_end)]
        known = [value for value in values if value is not None]
        data[name] = sum(known) if known else None
        data[f'{name}_complete'] = len(known) == len(values)
    return data


def _query_windows(cache: dict, now: datetime) -> list[tuple[datetime, datetime]]:
    """Refresh unsettled days and backfill one oldest missing contiguous week."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Include one day that can be finalized after at least two full days of reporting grace.
    recent_start = midnight - timedelta(days=SETTLE_DAYS + 1)
    finalized = set(cache.get('finalized_dates', []))
    while recent_start.date().isoformat() in finalized:
        recent_start += timedelta(days=1)
    windows = [(recent_start, now)]
    history_start = midnight - timedelta(days=HISTORY_DAYS - 1)
    historical_dates = _dates(history_start, midnight - timedelta(days=SETTLE_DAYS + 1))
    missing = [date for date in historical_dates if date not in finalized]
    if missing:
        start = datetime.fromisoformat(missing[0]).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        while (end - start).days < BACKFILL_DAYS and end < midnight - timedelta(days=SETTLE_DAYS + 1):
            if end.date().isoformat() in finalized:
                break
            end += timedelta(days=1)
        windows.append((start, end))
    return windows


def _get_token() -> str:
    """Obtain a management token without retries or logging authentication data."""
    tenant_id = os.environ['AZURE_TENANT_ID']
    if not re.fullmatch(r'[\w.-]+', tenant_id):
        raise ValueError('Invalid Azure tenant ID')
    log.info('Authenticating for Azure signing metrics.')
    response = requests.post(
        f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
        data={
            'client_id': os.environ['AZURE_CLIENT_ID'],
            'client_secret': os.environ['AZURE_CLIENT_SECRET'],
            'grant_type': 'client_credentials',
            'scope': 'https://management.azure.com/.default',
        },
        timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        log.warning('Azure signing authentication failed (HTTP %s).', response.status_code)
        raise ValueError('Azure authentication failed')
    return response.json()['access_token']


def _metric_error_category(detail: str) -> str:
    """Classify common errors using fixed labels, never returning response content."""
    detail = detail.casefold()
    for category, markers in METRIC_ERROR_CATEGORIES:
        if any(marker in detail for marker in markers):
            return category
    return 'unspecified'


def _redacted_metric_error(response: requests.Response, resource_id: str, token: str) -> str:
    """Expose structured Azure error details only after removing private values."""
    try:
        payload = response.json()
    except ValueError:
        return 'Azure returned a non-JSON error response.'
    if not isinstance(payload, dict):
        return NO_STRUCTURED_ERROR_DETAILS
    error = payload.get('error', payload)
    if not isinstance(error, dict):
        return NO_STRUCTURED_ERROR_DETAILS
    detail = '; '.join(f'{key}={error[key]}' for key in ('code', 'message') if isinstance(error.get(key), str))
    if not detail:
        return NO_STRUCTURED_ERROR_DETAILS
    detail = re.sub(r'https?://[^\s\x22\x27<>]+', '[REDACTED_URL]', detail, flags=re.IGNORECASE)
    detail = re.sub(r'/subscriptions/[^\s\x22\x27<>]+', '[REDACTED_RESOURCE]', detail, flags=re.IGNORECASE)
    private_values = [token, resource_id, *resource_id.split('/')[2:5:2], resource_id.rsplit('/', 1)[-1]]
    private_values.extend(os.getenv(key, '') for key in (
        RESOURCE_ID_ENV, 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_TENANT_ID',
    ))
    for private in sorted(set(filter(None, private_values)), key=len, reverse=True):
        detail = re.sub(re.escape(private), '[REDACTED]', detail, flags=re.IGNORECASE)
    detail = re.sub(
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        '[REDACTED_ID]', detail, flags=re.IGNORECASE,
    )
    detail = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', detail)
    return detail[:2000]


def _fetch_metrics(resource_id: str, token: str, start: datetime, end: datetime) -> dict:
    """Read one bounded metric window with no automatic retries."""
    log.info('Querying Azure signing metrics from %s to %s.', start.isoformat(), end.isoformat())
    # Azure decodes numeric UTC offsets as spaces; use the API's UTC Z notation.
    timespan = '/'.join(
        timestamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') for timestamp in (start, end)
    )
    response = requests.get(
        f'https://management.azure.com{resource_id}/providers/Microsoft.Insights/metrics',
        headers={'Authorization': f'Bearer {token}'},
        params={
            'api-version': '2023-10-01',
            'metricnames': 'SignCompleted',
            'aggregation': 'Total',
            # Use the metric's documented grain and the resource's default namespace.
            'interval': 'PT1M',
            'timespan': timespan,
        },
        timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        log.warning('Azure signing metrics request failed (HTTP %s).', response.status_code)
        log.warning('Azure signing metrics error category: %s.', _metric_error_category(response.text))
        if os.getenv('DASHBOARD_AZURE_SIGNING_DEBUG') == 'true':
            log.warning('Azure signing metrics error details: %s', _redacted_metric_error(response, resource_id, token))
        raise ValueError('Azure metrics request failed')
    return response.json()


def _metric_points(payload: dict):
    """Yield samples from successful signing metrics."""
    metrics = [metric for metric in payload['value'] if metric['name']['value'] == 'SignCompleted']
    if not metrics:
        raise ValueError('Signing metric missing from response')
    for metric in metrics:
        if metric.get('errorCode', 'Success') != 'Success':
            raise ValueError('Azure reported a metric error')
        for series in metric['timeseries']:
            yield from series['data']


def _signing_sample(point: dict, start: datetime, end: datetime) -> tuple[str, int] | None:
    """Validate a known count and return its UTC date within the requested window."""
    total = point.get('total')
    if total is None:
        return None
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        raise ValueError('Invalid signing count')
    if not math.isfinite(total) or total < 0 or not float(total).is_integer():
        raise ValueError('Invalid signing count')
    timestamp = datetime.fromisoformat(point['timeStamp'])
    if timestamp.tzinfo is None:
        raise ValueError('Metric timestamp must include a timezone')
    timestamp = timestamp.astimezone(timezone.utc)
    if start <= timestamp < end:
        return timestamp.date().isoformat(), int(total)
    return None


def _daily_counts(payload: dict, start: datetime, end: datetime) -> dict:
    """Sum samples by UTC day; absent telemetry remains unknown rather than zero."""
    days = dict.fromkeys(_dates(start, end))
    for point in _metric_points(payload):
        sample = _signing_sample(point, start, end)
        if sample is not None:
            date, total = sample
            days[date] = (days[date] or 0) + total
    return days


def _collect(cache: dict, resource_id: str, now: datetime) -> dict:
    """Merge successful windows by date, retaining finalized history indefinitely."""
    token = _get_token()
    days = {p['date']: p['completed'] for p in cache['daily']}
    finalized = set(cache.get('finalized_dates', []))
    finalize_before = now.date() - timedelta(days=SETTLE_DAYS)
    failed = False
    collected_at = cache.get('collected_at')
    for start, end in _query_windows(cache, now):
        try:
            counts = _daily_counts(_fetch_metrics(resource_id, token, start, end), start, end)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            log.warning('Azure signing metric window failed (%s).', type(error).__name__)
            failed = True
            continue
        log.info(
            'Azure signing metric window collected: %s of %s days have reported counts.',
            sum(total is not None for total in counts.values()), len(counts),
        )
        for date, total in counts.items():
            # A temporarily absent current sample must not erase a previously reported count.
            if total is not None or date not in days:
                days[date] = total
            if date < finalize_before.isoformat():
                finalized.add(date)
        collected_at = now.isoformat()
    return {
        **cache,
        'status': 'error' if failed else 'ready',
        'collected_at': collected_at,
        'daily': [{'date': date, 'completed': total} for date, total in sorted(days.items())],
        'finalized_dates': sorted(finalized),
    }


def _validate_configuration(resource_id: str) -> None:
    """Require a signing account resource ID and credentials before collecting."""
    if not RESOURCE_ID_PATTERN.fullmatch(resource_id):
        raise ValueError('Invalid Artifact Signing resource ID')
    if not all(os.getenv(key) for key in ('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET')):
        raise ValueError('Azure credentials are missing')


def _attempt_is_recent(cache: dict, now: datetime) -> bool:
    """Allow failed collections to retry; otherwise throttle recent attempts."""
    if cache.get('status') == 'error':
        log.info('Azure signing metrics: previous collection failed; retrying without the three-hour wait.')
        return False
    if not cache.get('attempted_at'):
        return False
    attempted_at = datetime.fromisoformat(cache['attempted_at'])
    return timedelta(0) <= now - attempted_at < REFRESH_INTERVAL


def update(base_dir: str) -> None:
    """Refresh optional metrics; preserve history and throttle successful collections."""
    log.info('Updating Azure signing metrics.')
    if os.getenv('DASHBOARD_AZURE_SIGNING_CACHE_ONLY') == 'true':
        log.info('Azure signing metrics: cache-only mode; no Azure requests will be made.')
        return
    resource_id = os.getenv(RESOURCE_ID_ENV, '').strip().rstrip('/')
    cache = _load_cache(base_dir)
    now = datetime.now(timezone.utc)
    if not resource_id:
        log.info('Azure signing metrics disabled: AZURE_SIGNING_RESOURCE_ID is not configured.')
        data = {'status': 'disabled', 'daily': []}
    else:
        resource_hash = hashlib.sha256(resource_id.lower().encode()).hexdigest()
        if cache.get('resource_hash') != resource_hash:
            cache = {'status': 'disabled', 'daily': []}
        try:
            _validate_configuration(resource_id)
            if _attempt_is_recent(cache, now):
                next_attempt = datetime.fromisoformat(cache['attempted_at']) + REFRESH_INTERVAL
                log.info(
                    'Azure signing metrics: using cached data; next Azure request allowed at %s.',
                    next_attempt.astimezone(timezone.utc).isoformat(),
                )
                return
            data = _collect(cache, resource_id, now)
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            log.warning('Azure signing collection failed (%s).', type(error).__name__)
            data = {**cache, 'status': 'error'}
        if data['status'] == 'error':
            log.warning('Azure signing metrics unavailable; check configuration, permissions, and connectivity.')
        data.update(resource_hash=resource_hash, attempted_at=now.isoformat())

    helpers.write_json_files(file_path=os.path.join(base_dir, 'azure', 'signing'), data=data)
    log.info('Azure signing metrics saved: status=%s, %s cached days.', data['status'], len(data['daily']))
