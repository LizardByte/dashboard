# standard imports
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

# lib imports
import pytest

# local imports
from src import pr_metrics


class FakeResponse:
    def __init__(self, payload=None, status=200, text='error', error=None):
        self.payload = payload if payload is not None else {}
        self.status_code = status
        self.text = text
        self.error = error

    def json(self):
        if self.error:
            raise self.error
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _node(number=1, state='OPEN', updated='2026-03-01T00:00:00Z', reviews=None, reactions=None):
    reviews = reviews if reviews is not None else []
    reactions = reactions if reactions is not None else []
    approvals = [review for review in reviews if review.get('state') == 'APPROVED']
    return {
        'number': number,
        'title': f'PR {number}',
        'url': f'https://github.com/LizardByte/demo/pull/{number}',
        'state': state,
        'isDraft': False,
        'createdAt': '2026-01-01T00:00:00Z',
        'updatedAt': updated,
        'closedAt': None,
        'mergedAt': None,
        'additions': 10,
        'deletions': 2,
        'changedFiles': 3,
        'reviewDecision': None,
        'author': {'login': 'author'},
        'reviews': {'totalCount': len(reviews), 'nodes': reviews},
        'approvals': {'nodes': approvals},
        'reactionGroups': reactions,
    }


def _pull(number, state='open', **overrides):
    pull = {
        'repository': 'demo',
        'number': number,
        'title': f'PR {number}',
        'url': f'https://github.com/LizardByte/demo/pull/{number}',
        'state': state,
        'draft': False,
        'author': 'author',
        'created_at': '2026-01-01T00:00:00+00:00',
        'updated_at': '2026-03-01T00:00:00+00:00',
        'closed_at': None,
        'merged_at': None,
        'additions': 10,
        'deletions': 2,
        'changed_files': 3,
        'labels': [],
        'review_count': 0,
        'review_decision': None,
        'first_review_at': None,
        'first_approval_at': None,
        'reactions': [],
    }
    pull.update(overrides)
    return pull


def test_datetime_and_active_repo_helpers():
    assert pr_metrics._parse_datetime(None) is None
    assert pr_metrics._parse_datetime('2026-01-01T00:00:00').tzinfo == timezone.utc
    parsed = pr_metrics._parse_datetime('2026-01-01T00:00:00Z')
    assert pr_metrics._isoformat(parsed) == '2026-01-01T00:00:00+00:00'
    assert pr_metrics._isoformat(None) is None
    now = datetime(2026, 1, 2, 3, 4, 5, 6789, tzinfo=timezone.utc)
    cutoff = pr_metrics._window_cutoff(now, 1)
    assert cutoff == datetime(2026, 1, 1, 3, 4, 5, tzinfo=timezone.utc)
    assert pr_metrics._search_timestamp(cutoff) == '2026-01-01T03:04:05Z'

    assert pr_metrics.is_active_repo({'topics': None})
    assert not pr_metrics.is_active_repo({'private': True})
    assert not pr_metrics.is_active_repo({'archived': True})
    assert not pr_metrics.is_active_repo({'topics': ['package-manager']})


def test_cache_helpers(monkeypatch, tmp_path):
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    assert pr_metrics.cache_path(str(tmp_path), 'x/demo').endswith(('prMetrics\\demo', 'prMetrics/demo'))
    assert pr_metrics.load_cache(str(tmp_path), 'demo') is None
    assert not pr_metrics.cache_is_fresh(None, now)

    path = tmp_path / 'github' / 'prMetrics' / 'demo.json'
    path.parent.mkdir(parents=True)
    path.write_text('{bad', encoding='utf-8')
    assert pr_metrics.load_cache(str(tmp_path), 'demo') is None
    path.write_text('[]', encoding='utf-8')
    assert pr_metrics.load_cache(str(tmp_path), 'demo') is None

    cache = {
        'cache_version': pr_metrics.CACHE_VERSION,
        'history_days': pr_metrics.HISTORY_DAYS,
        'collected_at': (now - timedelta(hours=1)).isoformat(),
        'pull_requests': [],
    }
    path.write_text(json.dumps(cache), encoding='utf-8')
    assert pr_metrics.load_cache(str(tmp_path), 'demo') == cache
    assert pr_metrics.cache_is_fresh(cache, now)

    assert not pr_metrics.cache_is_fresh({**cache, 'cache_version': 1}, now)
    assert not pr_metrics.cache_is_fresh({**cache, 'history_days': 1}, now)
    assert not pr_metrics.cache_is_fresh({**cache, 'collected_at': 'bad'}, now)
    assert not pr_metrics.cache_is_fresh(
        {**cache, 'collected_at': (now - pr_metrics.CACHE_MAX_AGE).isoformat()}, now)
    assert not pr_metrics.cache_is_fresh(
        {**cache, 'collected_at': (now + timedelta(seconds=1)).isoformat()}, now)


def test_graphql_connection_success_and_errors():
    connection = {'nodes': [], 'pageInfo': {'hasNextPage': False}}
    session = FakeSession([FakeResponse({'data': {'repository': {'pullRequests': connection}}})])
    assert pr_metrics._graphql_connection(session, {'A': 'b'}, {'name': 'demo'}) == connection
    assert session.calls[0]['url'] == pr_metrics.GRAPHQL_URL

    invalid_session = FakeSession([FakeResponse(error=ValueError('bad'))])
    with pytest.raises(RuntimeError, match='Invalid'):
        pr_metrics._graphql_connection(invalid_session, {}, {})

    graphql_error_session = FakeSession([FakeResponse({'errors': [{'message': 'bad'}]})])
    with pytest.raises(RuntimeError, match='failed'):
        pr_metrics._graphql_connection(graphql_error_session, {}, {})

    http_error_session = FakeSession([FakeResponse({'message': 'bad'}, status=500)])
    with pytest.raises(RuntimeError, match='failed'):
        pr_metrics._graphql_connection(http_error_session, {}, {})

    missing_repo_session = FakeSession([FakeResponse({'data': {'repository': None}})])
    with pytest.raises(RuntimeError, match='did not include'):
        pr_metrics._graphql_connection(missing_repo_session, {}, {})


def test_normalize_pull_handles_reviews_and_missing_optional_data():
    reviews = [
        {'state': 'COMMENTED', 'submittedAt': '2026-01-03T00:00:00Z', 'author': {'login': 'r1'}},
        {'state': 'APPROVED', 'submittedAt': '2026-01-02T00:00:00Z', 'author': {'login': 'r2'}},
        {'state': 'PENDING', 'submittedAt': None, 'author': None},
    ]
    node = _node(reviews=reviews, reactions=[
        {'content': 'THUMBS_UP', 'reactors': {'totalCount': 2}},
        {'content': 'HEART', 'reactors': {'totalCount': 0}},
        {'reactors': {'totalCount': 1}},
    ])
    normalized = pr_metrics._normalize_pull('demo', node)
    assert normalized['author'] == 'author'
    assert normalized['review_count'] == 3
    assert normalized['first_review_at'] == '2026-01-02T00:00:00+00:00'
    assert normalized['first_approval_at'] == '2026-01-02T00:00:00+00:00'
    assert normalized['reactions'] == [{'content': 'THUMBS_UP', 'count': 2}]

    minimal = _node(number=2, reviews=[])
    minimal.update({
        'title': None,
        'url': None,
        'state': None,
        'isDraft': 1,
        'additions': None,
        'deletions': None,
        'changedFiles': None,
        'author': None,
        'reviews': None,
        'approvals': None,
        'reactionGroups': None,
    })
    normalized_minimal = pr_metrics._normalize_pull('demo', minimal)
    assert normalized_minimal['title'] == ''
    assert normalized_minimal['state'] == ''
    assert normalized_minimal['draft'] is True
    assert normalized_minimal['author'] is None
    assert normalized_minimal['review_count'] == 0


def test_fetch_connection_paginates_and_stops_at_cutoff(monkeypatch):
    repo = SimpleNamespace(name='demo', owner=SimpleNamespace(login='LizardByte'))
    pages = [
        {
            'nodes': [_node(1, updated='2026-03-01T00:00:00Z')],
            'pageInfo': {'hasNextPage': True, 'endCursor': 'next'},
        },
        {
            'nodes': [
                _node(2, updated=None),
                _node(3, updated='2025-01-01T00:00:00Z'),
                _node(4, updated='2026-02-01T00:00:00Z'),
            ],
            'pageInfo': {'hasNextPage': True, 'endCursor': 'unused'},
        },
    ]
    variables = []

    def fake_connection(session, headers, current_variables):
        variables.append(current_variables)
        return pages.pop(0)

    monkeypatch.setattr(pr_metrics, '_graphql_connection', fake_connection)
    pulls = pr_metrics._fetch_connection(
        repo, {}, object(), ['CLOSED'], datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert [pull['number'] for pull in pulls] == [1, 2]
    assert variables[0]['cursor'] is None
    assert variables[1]['cursor'] == 'next'


def test_fetch_connection_requires_cursor(monkeypatch):
    repo = SimpleNamespace(name='demo', owner=SimpleNamespace(login='LizardByte'))
    monkeypatch.setattr(
        pr_metrics,
        '_graphql_connection',
        lambda *args: {'nodes': [], 'pageInfo': {'hasNextPage': True}},
    )
    with pytest.raises(RuntimeError, match='end cursor'):
        pr_metrics._fetch_connection(repo, {}, object(), ['OPEN'])


def test_fetch_repository_combines_deduplicates_and_sorts(monkeypatch):
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    repo = SimpleNamespace(name='demo', owner=SimpleNamespace(login='LizardByte'))
    calls = []

    def fake_fetch(repository, headers, session, states, cutoff=None):
        calls.append((states, cutoff))
        if states == ['OPEN']:
            return [_pull(1, updated_at='2026-03-01T00:00:00+00:00')]
        return [
            _pull(1, state='merged', updated_at='2026-02-01T00:00:00+00:00'),
            _pull(2, state='merged', updated_at='2026-03-10T00:00:00+00:00'),
        ]

    monkeypatch.setattr(pr_metrics, '_fetch_connection', fake_fetch)
    pulls = pr_metrics.fetch_repository(repo, {}, object(), now)

    assert [pull['number'] for pull in pulls] == [2, 1]
    assert calls[0] == (['OPEN'], None)
    assert calls[1][0] == ['CLOSED', 'MERGED']
    assert calls[1][1] == now - timedelta(days=pr_metrics.HISTORY_DAYS)


def test_refresh_repository_uses_fresh_cache_and_writes_stale_cache(monkeypatch, tmp_path):
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    repo = SimpleNamespace(name='demo', owner=SimpleNamespace(login='LizardByte'))
    fresh = {
        'repository': 'demo',
        'collected_at': (now - timedelta(hours=1)).isoformat(),
        'cache_version': pr_metrics.CACHE_VERSION,
        'history_days': pr_metrics.HISTORY_DAYS,
        'pull_requests': [],
    }
    path = tmp_path / 'github' / 'prMetrics' / 'demo.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(fresh), encoding='utf-8')
    monkeypatch.setattr(pr_metrics, 'fetch_repository', lambda *args: pytest.fail('unexpected fetch'))
    assert not pr_metrics.refresh_repository(repo, str(tmp_path), {}, object(), now)

    path.write_text(json.dumps({**fresh, 'collected_at': '2026-01-01T00:00:00+00:00'}), encoding='utf-8')
    monkeypatch.setattr(pr_metrics, 'fetch_repository', lambda *args: [_pull(1)])
    assert pr_metrics.refresh_repository(repo, str(tmp_path), {}, object(), now)
    written = json.loads(path.read_text(encoding='utf-8'))
    assert written['pull_requests'][0]['number'] == 1
    assert written['collected_at'] == now.isoformat()
    assert written['cache_version'] == pr_metrics.CACHE_VERSION

    path.unlink()
    assert pr_metrics.refresh_repository(repo, str(tmp_path), {}, object())


def test_calculation_and_formatting_helpers():
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    pulls = [
        _pull(1, draft=True),
        _pull(2, updated_at='2026-01-01T00:00:00+00:00'),
        _pull(3, review_count=1),
        _pull(4, review_count=1, review_decision='CHANGES_REQUESTED'),
        _pull(5, review_count=1, review_decision='APPROVED', first_approval_at='2026-01-02T00:00:00+00:00'),
        _pull(6, created_at=None, updated_at=None),
        _pull(
            7,
            state='merged',
            created_at='2026-02-01T00:00:00+00:00',
            updated_at='2026-02-04T00:00:00+00:00',
            merged_at='2026-02-04T00:00:00+00:00',
            closed_at='2026-02-04T00:00:00+00:00',
            review_count=1,
            first_review_at='2026-02-02T00:00:00+00:00',
            first_approval_at='2026-02-03T00:00:00+00:00',
        ),
        _pull(
            8,
            state='merged',
            created_at='2026-02-10T00:00:00+00:00',
            updated_at='2026-02-12T00:00:00+00:00',
            merged_at='2026-02-12T00:00:00+00:00',
            closed_at='2026-02-12T00:00:00+00:00',
        ),
        _pull(9, state='closed', closed_at='2026-02-15T00:00:00+00:00'),
        _pull(
            10,
            state='merged',
            created_at='2025-01-01T00:00:00+00:00',
            updated_at='2025-01-02T00:00:00+00:00',
            merged_at='2025-01-02T00:00:00+00:00',
        ),
    ]
    metrics = pr_metrics.calculate(pulls, now)

    assert metrics['open'] == 6
    assert metrics['draft'] == 1
    assert metrics['not_reviewed'] == 3
    assert metrics['awaiting_approval'] == 1
    assert metrics['changes_requested'] == 1
    assert metrics['stale'] == 1
    assert metrics['opened'] == 8
    assert metrics['merged'] == 2
    assert metrics['closed_unmerged'] == 1
    assert metrics['merge_rate'] == pytest.approx(200 / 3)
    assert metrics['median_merge_hours'] == pytest.approx(60)
    assert metrics['p75_merge_hours'] == pytest.approx(66)
    assert metrics['merged_without_review'] == 1
    assert metrics['merged_without_approval'] == 1
    assert metrics['additions'] == 20
    assert metrics['pending'][-1]['age_days'] == 0
    assert pr_metrics._is_awaiting_approval(_pull(11, review_decision='REVIEW_REQUIRED'))

    reopened = _pull(12, closed_at='2026-03-01T00:00:00+00:00')
    assert pr_metrics.calculate([reopened], now)['closed_unmerged'] == 0

    empty = pr_metrics.calculate([], now)
    assert empty['merge_rate'] is None
    assert empty['median_merge_hours'] is None
    assert pr_metrics._hours_between(None, None) is None
    assert pr_metrics._percentile([], 0.75) is None
    assert pr_metrics._percentile([4], 0.75) == 4
    assert pr_metrics._format_duration(None) == '—'
    assert pr_metrics._format_duration(12) == '12.0 h'
    assert pr_metrics._format_duration(48) == '2.0 d'
    assert pr_metrics._format_percent(None) == '—'
    assert pr_metrics._format_percent(50) == '50.0%'
    assert pr_metrics._markdown_text('a\\[b]|c\nd') == r'a\\\[b\]\|c d'
    assert pr_metrics._markdown_text(None) == ''
    org_search = pr_metrics._github_search(None, ['is:open', 'review:none'])
    assert 'org%3ALizardByte' in org_search
    assert 'review%3Anone' in org_search
    repo_search = pr_metrics._metric_link(2, 'demo', 'is:merged')
    assert repo_search.startswith('[2](https://github.com/pulls?q=')
    assert 'repo%3ALizardByte%2Fdemo' in repo_search
    assert pr_metrics._format_reactions(None) == '0'
    assert pr_metrics._format_reactions([
        {'content': 'CUSTOM', 'count': 1},
        {'content': 'HEART', 'count': 2},
        {'content': 'THUMBS_UP', 'count': 3},
        {'content': 'LAUGH', 'count': 0},
    ]) == '6 — 👍 3 ❤️ 2 CUSTOM 1'


def test_render_repository_page_covers_pending_statuses():
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    cache = {
        'collected_at': '2026-03-20T00:00:00+00:00',
        'pull_requests': [
            _pull(1, title='Needs | review', reactions=[
                {'content': 'THUMBS_UP', 'count': 2},
                {'content': 'HEART', 'count': 1},
            ]),
            _pull(2, review_count=1, review_decision='CHANGES_REQUESTED'),
            _pull(3, review_count=1, review_decision='APPROVED'),
            _pull(4, review_count=1),
            _pull(5, review_count=1, first_approval_at='2026-03-01T00:00:00+00:00'),
        ],
    }
    page = pr_metrics.render_repository_page('demo', cache, now)
    assert 'PR Metrics - demo' in page
    assert 'Needs \\| review' in page
    assert 'Not reviewed' in page
    assert 'Changes requested' in page
    assert 'Approved' in page
    assert 'Awaiting approval' in page
    assert '[3 — 👍 2 ❤️ 1](https://github.com/LizardByte/demo/pull/1)' in page
    assert "{{ '/pr-metrics/' | relative_url }}" in page
    assert '{{ site.baseurl }}' not in page
    assert 'pr-metrics-sortable' in page
    assert '  - /assets/js/pr-metrics.js' in page
    assert 'repo%3ALizardByte%2Fdemo' in page
    assert 'created%3A%3E%3D2025-12-20T00%3A00%3A00Z' in page
    assert '-review%3Anone' not in page
    assert '-review%3Aapproved' not in page

    unavailable = pr_metrics.render_repository_page('empty', None, now)
    assert 'have not been collected' in unavailable
    assert 'No ready pull requests' in unavailable

    invalid_timestamp = pr_metrics.render_repository_page(
        'invalid', {'collected_at': 'bad', 'pull_requests': []}, now)
    assert 'Data collected: **unavailable**' in invalid_timestamp


def test_render_index_and_write_report_pages(tmp_path):
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)
    caches = {
        'z-empty': None,
        'demo': {
            'collected_at': now.isoformat(),
            'pull_requests': [_pull(1)],
        },
    }
    index = pr_metrics.render_index_page(caches, now)
    assert 'Pull Request Metrics' in index
    assert index.index('[demo]') < index.index('[z-empty]')
    assert '⚠️' in index
    assert "{{ '/pr-metrics/demo/' | relative_url }}" in index
    assert '{{ site.baseurl }}' not in index
    assert 'org%3ALizardByte' in index
    assert 'pr-metrics-sortable' in index

    report_dir = tmp_path / 'pr-metrics'
    report_dir.mkdir()
    (report_dir / 'stale.md').write_text('old', encoding='utf-8')
    (report_dir / 'keep.txt').write_text('keep', encoding='utf-8')
    pr_metrics.write_report_pages(str(tmp_path), caches, now)

    assert (report_dir / 'index.md').exists()
    assert (report_dir / 'demo.md').exists()
    assert (report_dir / 'z-empty.md').exists()
    assert not (report_dir / 'stale.md').exists()
    assert (report_dir / 'keep.txt').exists()
