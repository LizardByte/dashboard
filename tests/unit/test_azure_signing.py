"""Prove incremental signing collection, cost bounds, and reporting accuracy."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote_plus, urlsplit

import pytest
import requests

from src import azure_signing as signing

RESOURCE = '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CodeSigning/codeSigningAccounts/account'
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def payload(*points, error='Success'):
    return {'value': [
        {'name': {'value': 'Other'}, 'timeseries': []},
        {'name': {'value': 'SignCompleted'}, 'errorCode': error, 'timeseries': [{'data': list(points)}]},
    ]}


def point(date, count):
    return {'timeStamp': date, 'total': count}


def write_cache(tmp_path, data):
    path = tmp_path / 'azure' / 'signing.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


@pytest.fixture
def configured(monkeypatch):
    for key, value in {
        signing.RESOURCE_ID_ENV: RESOURCE,
        'AZURE_TENANT_ID': 'tenant',
        'AZURE_CLIENT_ID': 'client',
        'AZURE_CLIENT_SECRET': 'secret-value',
    }.items():
        monkeypatch.setenv(key, value)

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(signing, 'datetime', Clock)
    return Clock


def test_disabled_and_invalid_cache(tmp_path, monkeypatch):
    monkeypatch.delenv(signing.RESOURCE_ID_ENV, raising=False)
    assert signing.load_data(str(tmp_path)) == {'status': 'disabled', 'daily': []}
    path = write_cache(tmp_path, {'status': 'ready', 'daily': [], 'secret': 'must-not-publish'})
    assert signing.load_data(str(tmp_path)) == {'status': 'ready', 'daily': []}
    for contents in ('invalid json', '[]', '{}'):
        path.write_text(contents)
        assert signing.load_data(str(tmp_path)) == {'status': 'disabled', 'daily': []}
    signing.update(str(tmp_path))
    assert json.loads(path.read_text()) == {'status': 'disabled', 'daily': []}


@pytest.mark.parametrize('has_cache', [False, True])
def test_cache_only_preview_preserves_data_without_requests(
        tmp_path, monkeypatch, configured, requests_mock, has_cache):
    messages = []
    monkeypatch.setattr(signing.log, 'info', lambda message, *args: messages.append(message % args))
    monkeypatch.setenv('DASHBOARD_AZURE_SIGNING_CACHE_ONLY', 'true')
    path = tmp_path / 'azure' / 'signing.json'
    if has_cache:
        write_cache(tmp_path, {
            'status': 'ready', 'resource_hash': hashlib.sha256(RESOURCE.lower().encode()).hexdigest(),
            'collected_at': (NOW - timedelta(days=1)).isoformat(),
            'attempted_at': (NOW - timedelta(days=1)).isoformat(),
            'daily': [{'date': '2026-10-06', 'completed': 7}],
            'finalized_dates': ['2026-10-06'],
        })
        original = path.read_bytes()

    signing.update(str(tmp_path))

    assert requests_mock.call_count == 0
    assert 'cache-only mode; no Azure requests' in '\n'.join(messages)
    if has_cache:
        assert path.read_bytes() == original
        assert signing.load_data(str(tmp_path))['daily'] == [{'date': '2026-10-06', 'completed': 7}]
    else:
        assert not path.exists()
        assert signing.load_data(str(tmp_path)) == {'status': 'disabled', 'daily': []}


def test_recent_success_logs_next_refresh_without_requests(tmp_path, monkeypatch, configured, requests_mock):
    messages = []
    monkeypatch.setattr(signing.log, 'info', lambda message, *args: messages.append(message % args))
    attempted_at = (NOW - timedelta(hours=2)).astimezone(timezone(timedelta(hours=-4)))
    path = write_cache(tmp_path, {
        'status': 'ready', 'daily': [{'date': '2026-10-06', 'completed': 0}],
        'collected_at': attempted_at.isoformat(), 'attempted_at': attempted_at.isoformat(),
        'resource_hash': hashlib.sha256(RESOURCE.lower().encode()).hexdigest(),
    })
    original = path.read_bytes()

    signing.update(str(tmp_path))

    assert requests_mock.call_count == 0
    assert path.read_bytes() == original
    assert f'next Azure request allowed at {(NOW + timedelta(hours=1)).isoformat()}' in '\n'.join(messages)
    assert all(private not in '\n'.join(messages) for private in ('secret-value', RESOURCE))


@pytest.mark.parametrize('outcome', ['success', 'failure', 'preview'])
@pytest.mark.parametrize('has_history', [False, True])
def test_failed_cache_retries_immediately(tmp_path, monkeypatch, configured, requests_mock, outcome, has_history):
    daily = [{'date': '2026-10-01', 'completed': 4}] if has_history else []
    collected_at = (NOW - timedelta(days=1)).isoformat() if has_history else None
    finalized = ['2026-10-01'] if has_history else []
    path = write_cache(tmp_path, {
        'status': 'error', 'daily': daily, 'collected_at': collected_at, 'finalized_dates': finalized,
        'attempted_at': (NOW - timedelta(minutes=10)).isoformat(),
        'resource_hash': hashlib.sha256(RESOURCE.lower().encode()).hexdigest(),
    })
    original = path.read_bytes()
    requests_mock.post('https://login.microsoftonline.com/tenant/oauth2/v2.0/token', json={'access_token': 'token'})
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    if outcome == 'failure':
        requests_mock.get(metrics_url, status_code=400)
    else:
        requests_mock.get(metrics_url, json=payload(point('2026-10-07T00:00:00Z', 7)))
    if outcome == 'preview':
        monkeypatch.setenv('DASHBOARD_AZURE_SIGNING_CACHE_ONLY', 'true')

    signing.update(str(tmp_path))

    if outcome == 'preview':
        assert requests_mock.call_count == 0
        assert path.read_bytes() == original
        return
    assert requests_mock.call_count == 3  # One token request and two metric queries, without in-run retries.
    saved = signing._load_cache(str(tmp_path))
    assert saved['attempted_at'] == NOW.isoformat()
    for cached_day in daily:
        assert cached_day in saved['daily']
    configured.current += timedelta(minutes=1)
    signing.update(str(tmp_path))
    if outcome == 'failure':
        assert saved['status'] == 'error'
        assert saved['daily'] == daily
        assert saved['collected_at'] == collected_at
        assert saved['finalized_dates'] == finalized
        assert requests_mock.call_count == 6
    else:
        assert saved['status'] == 'ready'
        assert saved['collected_at'] == NOW.isoformat()
        assert {'date': '2026-10-07', 'completed': 7} in saved['daily']
        assert requests_mock.call_count == 3  # A successful collection restores the three-hour throttle.


def test_public_totals_and_partial_history(tmp_path):
    write_cache(tmp_path, {
        'status': 'ready', 'resource_hash': 'private-cache-key', 'finalized_dates': ['2026-09-30'],
        'collected_at': NOW.isoformat(), 'attempted_at': NOW.isoformat(),
        'daily': [
            {'date': '2026-08-01', 'completed': 100},
            {'date': '2026-09-30', 'completed': 3},
            {'date': '2026-10-06', 'completed': None},
            {'date': '2026-10-07', 'completed': 0},
        ],
    })
    data = signing.load_data(str(tmp_path))
    assert 'resource_hash' not in data
    assert 'finalized_dates' not in data
    assert data['month_to_date'] == 0
    assert data['last_30_days'] == 3
    assert not data['month_to_date_complete']
    assert not data['last_30_days_complete']

    # A new month must not include the previous month in its total.
    write_cache(tmp_path, {
        'status': 'ready', 'collected_at': '2026-11-01T12:00:00+00:00',
        'daily': [{'date': '2026-10-31', 'completed': 9}],
    })
    assert signing.load_data(str(tmp_path))['month_to_date'] is None
    write_cache(tmp_path, {
        'status': 'ready', 'collected_at': '2026-10-31T12:00:00+00:00',
        'daily': [{'date': '2026-10-31', 'completed': 9}],
    })
    assert signing.load_data(str(tmp_path))['month_to_date'] == 9


def test_query_windows_never_cross_finalized_history():
    current, backfill = signing._query_windows({}, NOW)
    assert current == (datetime(2026, 10, 4, tzinfo=timezone.utc), NOW)
    assert (backfill[1] - backfill[0]).days == 7
    assert (NOW.date() - backfill[0].date()).days == 89
    oldest = backfill[0]
    finalized = [(oldest + timedelta(days=i)).date().isoformat() for i in (0, 2)] + ['2026-10-04']
    current, backfill = signing._query_windows({'finalized_dates': finalized}, NOW)
    assert current[0].date().isoformat() == '2026-10-05'
    assert backfill == (oldest + timedelta(days=1), oldest + timedelta(days=2))

    finalized = signing._dates(oldest, datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert signing._query_windows({'finalized_dates': finalized}, NOW) == [current]
    finalized.remove('2026-10-03')
    assert signing._query_windows({'finalized_dates': finalized}, NOW)[1] == (
        datetime(2026, 10, 3, tzinfo=timezone.utc), datetime(2026, 10, 4, tzinfo=timezone.utc),
    )


def test_daily_counts_adjusted_grain_timezone_and_unknown_samples():
    start = datetime(2026, 10, 4, tzinfo=timezone.utc)
    data = payload(
        point('2026-10-04T00:00:00Z', 2), point('2026-10-04T01:00:00Z', 3),
        point('2026-10-04T23:30:00-02:00', 4), point('2026-10-06T00:00:00Z', None),
        point('2026-10-07T00:00:00Z', 0), point('2026-10-03T23:59:00Z', 99),
        point(NOW.isoformat(), 99),
    )
    data['value'][1]['timeseries'].append({'data': [point('2026-10-04T03:00:00Z', 1)]})
    assert signing._daily_counts(data, start, NOW) == {
        '2026-10-04': 6, '2026-10-05': 4, '2026-10-06': None, '2026-10-07': 0,
    }
    assert signing._dates(start, start) == []
    assert signing._daily_counts(payload(), start, NOW)['2026-10-04'] is None


@pytest.mark.parametrize('count', [True, '3', float('nan'), float('inf'), -1, 0.5])
def test_invalid_count_is_rejected(count):
    data = payload(point('2026-10-07T00:00:00Z', count))
    start = NOW - timedelta(days=1)
    with pytest.raises(ValueError, match='Invalid signing count'):
        signing._daily_counts(data, start, NOW)


def test_unavailable_or_malformed_metrics_are_not_finalized():
    start = NOW - timedelta(days=1)
    for data in ({'value': []}, payload(error='Error'), payload(point('2026-10-07T00:00:00', 1))):
        with pytest.raises(ValueError):
            signing._daily_counts(data, start, NOW)


def test_http_contract_and_no_credential_disclosure(requests_mock, configured, monkeypatch):
    warnings = []
    messages = []
    monkeypatch.setattr(signing.log, 'warning', lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr(signing.log, 'info', lambda message, *args: messages.append(message % args))
    token_url = 'https://login.microsoftonline.com/tenant/oauth2/v2.0/token'
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    requests_mock.post(token_url, json={'access_token': 'private-token'})
    requests_mock.get(metrics_url, json=payload())
    token = signing._get_token()
    start = NOW - timedelta(days=1)
    assert signing._fetch_metrics(RESOURCE, token, start, NOW) == payload()
    assert requests_mock.call_count == 2
    request = requests_mock.last_request
    assert request.headers['Authorization'] == 'Bearer private-token'
    assert request.qs['metricnames'] == ['signcompleted']
    assert request.qs['aggregation'] == ['total']
    assert request.qs['interval'] == ['pt1m']
    assert 'metricnamespace' not in request.qs
    assert 'autoadjusttimegrain' not in request.qs
    assert request.qs['timespan'] == ['2026-10-06t12:00:00z/2026-10-07t12:00:00z']
    assert 'private-token' not in request.url

    requests_mock.post(token_url, status_code=401, text='private-token secret-value')
    with pytest.raises(ValueError, match='Azure authentication failed') as error:
        signing._get_token()
    assert 'secret-value' not in str(error.value)
    requests_mock.get(metrics_url, status_code=403, text=f'private-token secret-value {RESOURCE}')
    with pytest.raises(ValueError, match='Azure metrics request failed'):
        signing._fetch_metrics(RESOURCE, token, start, NOW)
    assert warnings == [
        'Azure signing authentication failed (HTTP 401).',
        'Azure signing metrics request failed (HTTP 403).',
        'Azure signing metrics error category: unspecified.',
    ]
    assert all(private not in '\n'.join(warnings + messages) for private in ('private-token', 'secret-value', RESOURCE))


@pytest.mark.parametrize('offset_hours', [0, -4, 5.5])
def test_timespan_survives_azure_query_decoding(requests_mock, offset_hours):
    start = datetime(2026, 10, 5, tzinfo=timezone.utc)
    end = datetime(2026, 10, 8, 3, 59, 8, 13470, tzinfo=timezone.utc)
    offset = timezone(timedelta(hours=offset_hours))
    expected = '2026-10-05T00:00:00Z/2026-10-08T03:59:08.013470Z'

    def azure_response(request, context):
        # The live rejection shows Azure decoding the timestamp's '+' as a space.
        timespan = unquote_plus(parse_qs(urlsplit(request.url).query)['timespan'][0])
        assert timespan == expected
        return payload()

    requests_mock.get(
        f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics', json=azure_response,
    )

    assert signing._fetch_metrics(RESOURCE, 'token', start.astimezone(offset), end.astimezone(offset)) == payload()
    assert requests_mock.call_count == 1


@pytest.mark.parametrize('debug', ['false', 'true'])
@pytest.mark.parametrize('has_points', [False, True])
def test_debug_response_sample_counts_do_not_disclose_raw_metrics(requests_mock, monkeypatch, debug, has_points):
    messages = []
    monkeypatch.setenv('DASHBOARD_AZURE_SIGNING_DEBUG', debug)
    monkeypatch.setattr(signing.log, 'info', lambda message, *args: messages.append(message % args))
    data = payload()
    if has_points:
        data = payload(
            point('2026-10-07T00:00:00Z', 17), point('2026-10-07T00:01:00Z', None),
            {'timeStamp': '2026-10-07T00:02:00Z', 'count': 0},
        )
    data['value'][1]['id'] = RESOURCE
    data['value'][1]['timeseries'][0]['metadatavalues'] = [{'name': 'TenantId', 'value': 'private-tenant'}]
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    requests_mock.get(metrics_url, json=data)
    start = NOW - timedelta(days=1)

    assert signing._fetch_metrics(RESOURCE, 'private-token', start, NOW) == data
    assert requests_mock.call_count == 1
    summaries = [message for message in messages if 'response samples:' in message]
    if debug == 'false':
        assert summaries == []
    else:
        assert summaries == [
            'Azure signing response samples: 3 points; 1 with total; 1 with count.' if has_points
            else 'Azure signing response samples: 0 points; 0 with total; 0 with count.',
        ]
    assert all(private not in '\n'.join(messages) for private in (RESOURCE, 'private-token', 'private-tenant', '17'))


@pytest.mark.parametrize(('detail', 'category'), [
    ('Unsupported TimeGrain', 'time interval'),
    ('Failed to find metric configuration for provider', 'namespace'),
    ('Failed to find metric named SignCompleted', 'metric name'),
    ('Unsupported aggregation type', 'aggregation'),
    ('Invalid timespan', 'time range'),
    ('Unrecognized error', 'unspecified'),
])
def test_query_error_categories_do_not_disclose_response_details(
        requests_mock, configured, monkeypatch, detail, category):
    warnings = []
    monkeypatch.setattr(signing.log, 'warning', lambda message, *args: warnings.append(message % args))
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    requests_mock.get(metrics_url, status_code=400, json={
        'error': {'code': 'BadRequest', 'message': f'{detail}: {RESOURCE} secret-value private-token'},
    })

    start = NOW - timedelta(days=1)
    with pytest.raises(ValueError, match='Azure metrics request failed'):
        signing._fetch_metrics(RESOURCE, 'private-token', start, NOW)

    assert requests_mock.call_count == 1
    assert warnings[-1] == f'Azure signing metrics error category: {category}.'
    assert all(private not in '\n'.join(warnings) for private in (detail, RESOURCE, 'secret-value', 'private-token'))


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('debug', ['false', 'true'])
def test_debug_error_details_preserve_reason_and_redact_identifiers(
        requests_mock, configured, monkeypatch, nested, debug):
    warnings = []
    monkeypatch.setattr(signing.log, 'warning', lambda message, *args: warnings.append(message % args))
    monkeypatch.setenv('DASHBOARD_AZURE_SIGNING_DEBUG', debug)
    other_id = '12345678-1234-1234-1234-123456789012'
    private_url = 'https://example.invalid/private?credential=encoded-value'
    error = {'code': 'BadRequest', 'message': (
        'Requested time interval PT1M; supported intervals: PT1H, P1D.\n'
        f'{RESOURCE.upper()} SUB RG ACCOUNT TENANT CLIENT secret-value private-token {other_id} {private_url} '
        '/subscriptions/another-id/resourceGroups/another-group/providers/type/name'
    )}
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    requests_mock.get(metrics_url, status_code=400, json={'error': error} if nested else error)
    start = NOW - timedelta(days=1)

    with pytest.raises(ValueError, match='Azure metrics request failed'):
        signing._fetch_metrics(RESOURCE, 'private-token', start, NOW)

    assert requests_mock.call_count == 1
    details = [message for message in warnings if 'error details:' in message]
    if debug == 'false':
        assert details == []
        return
    assert len(details) == 1
    assert 'code=BadRequest; message=Requested time interval PT1M; supported intervals: PT1H, P1D.' in details[0]
    assert all(private not in details[0].casefold() for private in (
        RESOURCE.casefold(), 'sub', 'rg', 'account', 'tenant', 'client', 'secret-value', 'private-token',
        other_id, private_url, 'another-id', 'another-group',
    ))
    assert '\n' not in details[0]


@pytest.mark.parametrize('error_body', [None, [], {'error': 'private-token'}, {'code': 3, 'message': []}])
def test_debug_error_details_ignore_unstructured_responses(requests_mock, configured, monkeypatch, error_body):
    monkeypatch.setenv('DASHBOARD_AZURE_SIGNING_DEBUG', 'true')
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    if error_body is None:
        requests_mock.get(metrics_url, status_code=400, text='private-token secret-value')
    else:
        requests_mock.get(metrics_url, status_code=400, json=error_body)
    response = signing.requests.get(metrics_url)

    detail = signing._redacted_metric_error(response, RESOURCE, 'private-token')

    assert detail in ('Azure returned a non-JSON error response.', 'Azure returned no structured error details.')
    assert 'private-token' not in detail
    assert 'secret-value' not in detail


def test_debug_error_details_are_bounded(requests_mock, configured):
    metrics_url = f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics'
    requests_mock.get(metrics_url, status_code=400, json={'message': 'x' * 5000 + ' private-token'})
    response = signing.requests.get(metrics_url)

    detail = signing._redacted_metric_error(response, RESOURCE, 'private-token')

    assert len(detail) == 2000
    assert 'private-token' not in detail


def test_bad_tenant_cannot_redirect_credentials(monkeypatch, configured):
    monkeypatch.setenv('AZURE_TENANT_ID', 'tenant/../../evil')
    with pytest.raises(ValueError, match='Invalid Azure tenant ID'):
        signing._get_token()


def test_incremental_refresh_rollover_and_archival(monkeypatch, tmp_path, configured):
    calls = []
    count = 2
    monkeypatch.setattr(signing, '_get_token', lambda: 'token')

    def fetch(resource_id, token, start, end):
        calls.append((start, end))
        return payload(*(point(f'{date}T00:00:00Z', count) for date in signing._dates(start, end)))

    monkeypatch.setattr(signing, '_fetch_metrics', fetch)
    signing.update(str(tmp_path))
    first = signing._load_cache(str(tmp_path))
    assert len(calls) == 2
    assert len(first['finalized_dates']) == 8
    assert '2026-10-04' in first['finalized_dates']
    assert '2026-10-05' not in first['finalized_dates']

    # Repeated builds within the polling interval consume no Azure requests.
    signing.update(str(tmp_path))
    assert len(calls) == 2

    configured.current += timedelta(hours=3)
    count = 5
    signing.update(str(tmp_path))
    second = signing._load_cache(str(tmp_path))
    assert len(calls) == 4
    assert calls[2][0].date().isoformat() == '2026-10-05'
    first_week_end = calls[1][1]
    assert calls[3][0] == first_week_end
    days = {p['date']: p['completed'] for p in second['daily']}
    assert days['2026-10-04'] == 2  # Archived day stays unchanged.
    assert days['2026-10-07'] == 5  # Replacement, never 2 + 5.

    configured.current = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    signing.update(str(tmp_path))
    third = signing._load_cache(str(tmp_path))
    assert '2026-10-05' in third['finalized_dates']
    assert len(third['daily']) > len(second['daily'])

    # Old history survives beyond Azure's retention window and the current query skips it.
    third['daily'].insert(0, {'date': '2025-01-01', 'completed': 50})
    third['finalized_dates'] = signing._dates(
        configured.current.replace(hour=0) - timedelta(days=89),
        configured.current.replace(hour=0) - timedelta(days=2),
    )
    write_cache(tmp_path, third)
    configured.current += timedelta(hours=3)
    before = len(calls)
    signing.update(str(tmp_path))
    assert len(calls) == before + 1
    assert signing._load_cache(str(tmp_path))['daily'][0] == {'date': '2025-01-01', 'completed': 50}


def test_failed_backfill_keeps_current_and_retries_gap(monkeypatch, tmp_path, configured):
    warnings = []
    monkeypatch.setattr(signing.log, 'warning', lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr(signing, '_get_token', lambda: 'token')
    windows = []

    def fetch(resource_id, token, start, end):
        windows.append((start, end))
        if end != configured.current:
            raise requests.Timeout('secret-value private-token')
        return payload(point('2026-10-07T00:00:00Z', 8))

    monkeypatch.setattr(signing, '_fetch_metrics', fetch)
    signing.update(str(tmp_path))
    data = signing.load_data(str(tmp_path))
    assert data['status'] == 'error'
    assert data['month_to_date'] == 8
    assert not data['month_to_date_complete']
    assert 'secret-value' not in json.dumps(data)
    assert windows[1][0].date().isoformat() not in signing._load_cache(str(tmp_path))['finalized_dates']
    configured.current += timedelta(hours=3)
    signing.update(str(tmp_path))
    assert windows[3] == windows[1]
    assert warnings.count('Azure signing metric window failed (Timeout).') == 2
    assert all(private not in '\n'.join(warnings) for private in ('private-token', 'secret-value', RESOURCE))


def test_absent_recent_samples_preserve_previous_counts(monkeypatch):
    monkeypatch.setattr(signing, '_get_token', lambda: 'token')
    monkeypatch.setattr(signing, '_fetch_metrics', lambda *args: payload())
    data = signing._collect({'daily': [{'date': '2026-10-07', 'completed': 9}]}, RESOURCE, NOW)
    assert next(p['completed'] for p in data['daily'] if p['date'] == '2026-10-07') == 9
    assert next(p['completed'] for p in data['daily'] if p['date'] == '2026-10-06') is None


def test_current_failure_can_still_backfill(monkeypatch):
    monkeypatch.setattr(signing, '_get_token', lambda: 'token')

    def fetch(resource_id, token, start, end):
        if end == NOW:
            return {'value': []}
        return payload(point(start.isoformat(), 3))

    monkeypatch.setattr(signing, '_fetch_metrics', fetch)
    result = signing._collect({'daily': []}, RESOURCE, NOW)
    assert result['status'] == 'error'
    assert result['collected_at'] == NOW.isoformat()
    assert len(result['finalized_dates']) == 7


@pytest.mark.parametrize('failure', ['credentials', 'resource', 'authentication', 'cache_timestamp'])
def test_update_failures_preserve_matching_cache(monkeypatch, tmp_path, configured, requests_mock, failure):
    cache = {
        'status': 'ready', 'daily': [{'date': '2026-10-01', 'completed': 4}],
        'collected_at': (NOW - timedelta(days=1)).isoformat(),
        'resource_hash': hashlib.sha256(RESOURCE.lower().encode()).hexdigest(),
    }
    if failure == 'credentials':
        monkeypatch.delenv('AZURE_CLIENT_SECRET')
    elif failure == 'resource':
        monkeypatch.setenv(signing.RESOURCE_ID_ENV, 'https://evil.example/resource')
    elif failure == 'cache_timestamp':
        cache['attempted_at'] = 'not-a-date'
        requests_mock.post('https://login.microsoftonline.com/tenant/oauth2/v2.0/token', json={'access_token': 'token'})
        requests_mock.get(
            f'https://management.azure.com{RESOURCE}/providers/Microsoft.Insights/metrics', json=payload(),
        )
    else:
        def bad_token():
            raise requests.Timeout('secret-value')
        monkeypatch.setattr(signing, '_get_token', bad_token)
    path = write_cache(tmp_path, cache)
    signing.update(str(tmp_path))
    saved = json.loads(path.read_text())
    assert saved['status'] == 'error'
    assert saved['attempted_at'] == NOW.isoformat()
    if failure == 'resource':
        assert saved['daily'] == []  # Never reuse another account's history.
    else:
        assert saved['daily'] == cache['daily']
    signing.update(str(tmp_path))
    if failure == 'cache_timestamp':
        assert requests_mock.call_count == 3
        assert signing._load_cache(str(tmp_path))['status'] == 'ready'
    else:
        assert requests_mock.call_count == 0


def test_request_options_disable_redirects_and_retries(monkeypatch, configured):
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {'access_token': 'token'})

    monkeypatch.setattr(signing.requests, 'post', request)
    monkeypatch.setattr(signing.requests, 'get', request)
    signing._get_token()
    signing._fetch_metrics(RESOURCE, 'token', NOW - timedelta(days=1), NOW)
    assert len(calls) == 2
    assert all(options['timeout'] == 30 and options['allow_redirects'] is False for _, options in calls)
