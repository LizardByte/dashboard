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
RESOURCE_ID_PATTERN = re.compile(
    r'/subscriptions/[\w-]+/resourceGroups/[\w.()-]+/'
    r'providers/Microsoft\.CodeSigning/codeSigningAccounts/[\w-]+', re.IGNORECASE,
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
        raise ValueError('Azure authentication failed')
    return response.json()['access_token']


def _fetch_metrics(resource_id: str, token: str, start: datetime, end: datetime) -> dict:
    """Read one bounded metric window with no automatic retries."""
    response = requests.get(
        f'https://management.azure.com{resource_id}/providers/Microsoft.Insights/metrics',
        headers={'Authorization': f'Bearer {token}'},
        params={
            'api-version': '2023-10-01',
            'metricnamespace': 'Microsoft.CodeSigning/codeSigningAccounts',
            'metricnames': 'SignCompleted',
            'aggregation': 'Total',
            'interval': 'P1D',
            'AutoAdjustTimegrain': 'true',
            'timespan': f'{start.isoformat()}/{end.isoformat()}',
        },
        timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError('Azure metrics request failed')
    return response.json()


def _daily_counts(payload: dict, start: datetime, end: datetime) -> dict:
    """Sum samples by UTC day; absent telemetry remains unknown rather than zero."""
    days = dict.fromkeys(_dates(start, end))
    metrics = [metric for metric in payload['value'] if metric['name']['value'] == 'SignCompleted']
    if not metrics:
        raise ValueError('Signing metric missing from response')
    for metric in metrics:
        if metric.get('errorCode', 'Success') != 'Success':
            raise ValueError('Azure reported a metric error')
        for series in metric['timeseries']:
            for point in series['data']:
                total = point.get('total')
                if total is None:
                    continue
                if isinstance(total, bool) or not isinstance(total, (int, float)):
                    raise ValueError('Invalid signing count')
                if not math.isfinite(total) or total < 0 or not float(total).is_integer():
                    raise ValueError('Invalid signing count')
                timestamp = datetime.fromisoformat(point['timeStamp'])
                if timestamp.tzinfo is None:
                    raise ValueError('Metric timestamp must include a timezone')
                timestamp = timestamp.astimezone(timezone.utc)
                if start <= timestamp < end:
                    date = timestamp.date().isoformat()
                    days[date] = (days[date] or 0) + int(total)
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
        except (requests.RequestException, ValueError, KeyError, TypeError):
            failed = True
            continue
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


def update(base_dir: str) -> None:
    """Refresh optional metrics; preserve successful data and throttle every attempt."""
    resource_id = os.getenv(RESOURCE_ID_ENV, '').strip().rstrip('/')
    cache = _load_cache(base_dir)
    now = datetime.now(timezone.utc)
    if not resource_id:
        data = {'status': 'disabled', 'daily': []}
    else:
        resource_hash = hashlib.sha256(resource_id.lower().encode()).hexdigest()
        if cache.get('resource_hash') != resource_hash:
            cache = {'status': 'disabled', 'daily': []}
        try:
            if not RESOURCE_ID_PATTERN.fullmatch(resource_id):
                raise ValueError('Invalid Artifact Signing resource ID')
            if not all(os.getenv(key) for key in ('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET')):
                raise ValueError('Azure credentials are missing')
            if cache.get('attempted_at'):
                attempted_at = datetime.fromisoformat(cache['attempted_at'])
                if timedelta(0) <= now - attempted_at < REFRESH_INTERVAL:
                    return
            data = _collect(cache, resource_id, now)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            data = {**cache, 'status': 'error'}
        if data['status'] == 'error':
            log.warning('Azure signing metrics unavailable; check configuration, permissions, and connectivity.')
        data.update(resource_hash=resource_hash, attempted_at=now.isoformat())

    helpers.write_json_files(file_path=os.path.join(base_dir, 'azure', 'signing'), data=data)
