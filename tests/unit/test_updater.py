# standard imports
import json
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from types import SimpleNamespace

# lib imports
from github.GithubException import GithubException
import pytest
import requests

# local imports
from src import updater


class FakeResponse:
    def __init__(self, payload=None, status=200, text='error', raises=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text
        self._raises = raises

    def json(self):
        if self._raises:
            raise self._raises
        return self._payload


class FakeWeek:
    def __init__(self, week, total):
        self.raw_data = {'week': week, 'total': total}


class FakePull:
    def __init__(self, number=1):
        self.number = number
        self.title = 'PR'
        self.user = SimpleNamespace(login='author')
        self.labels = [SimpleNamespace(name='label')]
        self.assignees = [SimpleNamespace(login='assignee')]
        self.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.draft = False
        self.milestone = None


class FakeStargazer:
    def __init__(self, date):
        self.starred_at = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)


class FakeStargazers:
    def __init__(self, pages, fail_page=None):
        self.pages = pages
        self.fail_page = fail_page

    def get_page(self, idx):
        if self.fail_page is not None and idx == self.fail_page:
            raise RuntimeError('boom')
        return self.pages.get(idx, [])


class FakeRepo:
    def __init__(self, name='repo1', archived=False, stars=4):
        self.name = name
        self.archived = archived
        self.owner = SimpleNamespace(login='owner')
        self.stargazers_count = stars
        self.raw_data = {'name': name, 'archived': archived}

    def get_languages(self):
        return {'Python': 100}

    def get_stats_commit_activity(self):
        return [FakeWeek(1, 1)]

    def get_pulls(self, state='open'):
        assert state == 'open'
        return [FakePull(3)]

    def get_stargazers_with_dates(self):
        return FakeStargazers({
            0: [FakeStargazer('2026-01-01'), FakeStargazer('2026-01-02')],
            1: [FakeStargazer('2026-01-03')],
        })

    def get_codescan_alerts(self, **kwargs):
        return []


def test_update_aur(monkeypatch):
    written = []
    monkeypatch.setattr(updater.helpers.s, 'get', lambda url: FakeResponse({'result': url}))
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: written.append((file_path, data)))
    monkeypatch.setattr(updater, 'BASE_DIR', 'base')

    updater.update_aur(['a', 'b'])

    assert len(written) == 2
    assert written[0][0].endswith('aur\\a') or written[0][0].endswith('aur/a')


def test_process_coverage_response(monkeypatch):
    good = FakeResponse({'results': [1], 'next': 'x'}, 200)
    assert updater.process_coverage_response(good, 'r') == ([1], True)

    empty = FakeResponse({'results': []}, 200)
    assert updater.process_coverage_response(empty, 'r') == ([], False)

    bad = FakeResponse(raises=RuntimeError('x'))
    warnings = []
    monkeypatch.setattr(updater.log, 'warning', lambda msg: warnings.append(msg))
    assert updater.process_coverage_response(bad, 'r') == ([], False)
    assert warnings


def test_fetch_coverage_trend_for_repo(monkeypatch):
    calls = []

    def fake_get(url, headers, params):
        calls.append(params['page'])
        if params['page'] == 1:
            return FakeResponse({'results': [{'avg': 1}], 'next': 'n'}, 200)
        return FakeResponse({'results': [], 'next': None}, 200)

    monkeypatch.setattr(updater.helpers.s, 'get', fake_get)
    out = updater.fetch_coverage_trend_for_repo('https://x', 'repo', {'h': 1})

    assert out == [{'avg': 1}]
    assert calls == [1, 2]


def test_update_codecov_success(monkeypatch, tmp_path):
    base = tmp_path / 'gh-pages'
    repos_file = base / 'github' / 'repos.json'
    repos_file.parent.mkdir(parents=True)
    repos_file.write_text(json.dumps([{'name': 'archived-repo', 'archived': True}]), encoding='utf-8')

    monkeypatch.setattr(updater, 'BASE_DIR', str(base))
    monkeypatch.setenv('CODECOV_TOKEN', 'tok')
    monkeypatch.setenv('GITHUB_REPOSITORY_OWNER', 'owner')

    writes = []
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))
    monkeypatch.setattr(updater, 'fetch_coverage_trend_for_repo', lambda *args, **kwargs: [{'avg': 90}])

    def fake_get(url, headers):
        if url.endswith('/repos?page_size=500'):
            return FakeResponse({'next': None, 'results': [{'name': 'archived-repo'}, {'name': 'active-repo'}]}, 200)
        return FakeResponse({'name': 'active-repo', 'totals': {'coverage': 90}}, 200)

    monkeypatch.setattr(updater.helpers.s, 'get', fake_get)

    updater.update_codecov()

    assert any('active-repo' in path for path, _ in writes)
    assert not any('archived-repo' in path for path, _ in writes)
    assert any(path.endswith('active-repo_coverage_trend') for path, _ in writes)


def test_update_codecov_error_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path / 'gh-pages'))
    monkeypatch.setenv('CODECOV_TOKEN', 'tok')
    monkeypatch.setenv('GITHUB_REPOSITORY_OWNER', 'owner')

    bad_repos = tmp_path / 'gh-pages' / 'github' / 'repos.json'
    bad_repos.parent.mkdir(parents=True, exist_ok=True)
    bad_repos.write_text('{bad', encoding='utf-8')
    warnings = []
    monkeypatch.setattr(updater.log, 'warning', lambda msg: warnings.append(msg))

    monkeypatch.setattr(
        updater.helpers.s,
        'get',
        lambda url, headers: FakeResponse(raises=requests.exceptions.JSONDecodeError('x', 'y', 0), text='bad')
    )
    with pytest.raises(requests.exceptions.HTTPError):
        updater.update_codecov()

    monkeypatch.setattr(updater.helpers.s, 'get', lambda url, headers: FakeResponse({'detail': 'boom'}, 500))
    with pytest.raises(requests.exceptions.HTTPError):
        updater.update_codecov()

    monkeypatch.setattr(
        updater.helpers.s,
        'get',
        lambda url, headers: FakeResponse({'next': 'more', 'results': []}, 200)
    )
    with pytest.raises(AssertionError):
        updater.update_codecov()

    assert warnings


def test_update_discord(monkeypatch):
    monkeypatch.setenv('DISCORD_INVITE', 'invite')
    writes = []
    monkeypatch.setattr(updater.helpers.s, 'get', lambda url: FakeResponse({'code': 'invite'}))
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))
    monkeypatch.setattr(updater, 'BASE_DIR', 'base')

    updater.update_discord()

    assert any(
        file_path.endswith('discord\\invite') or file_path.endswith('discord/invite')
        for file_path, _ in writes
    )


def test_update_fb(monkeypatch):
    monkeypatch.setenv('FACEBOOK_TOKEN', 'tok')
    monkeypatch.setenv('FACEBOOK_GROUP_ID', 'g')
    monkeypatch.setenv('FACEBOOK_PAGE_ID', 'p')
    monkeypatch.setattr(updater, 'BASE_DIR', 'base')

    writes = []

    def fake_get(url):
        if '/g?' in url:
            return FakeResponse({'name': 'grp', 'paging': {'x': 1}})
        return FakeResponse({'data': [{'value': 1}]})

    monkeypatch.setattr(updater.helpers.s, 'get', fake_get)
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))

    updater.update_fb()

    assert len(writes) == 2
    assert 'paging' not in writes[0][1]


def test_get_stats_with_timeout_success_and_timeout(monkeypatch):
    class FutureOk:
        def result(self, timeout):
            return [1]

    class FutureTimeout:
        def result(self, timeout):
            raise FuturesTimeout()

    class Pool:
        def __init__(self, future):
            self.future = future

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, func):
            return self.future

    monkeypatch.setattr(updater, 'ThreadPoolExecutor', lambda max_workers: Pool(FutureOk()))
    repo = SimpleNamespace(name='x', get_stats_commit_activity=lambda: [1])
    assert updater._get_stats_with_timeout(repo) == [1]

    warnings = []
    monkeypatch.setattr(updater.log, 'warning', lambda msg: warnings.append(msg))
    monkeypatch.setattr(updater, 'ThreadPoolExecutor', lambda max_workers: Pool(FutureTimeout()))
    assert updater._get_stats_with_timeout(repo) is None
    assert warnings


def test_seed_star_history(monkeypatch):
    repo = FakeRepo(stars=250)
    history = updater._seed_star_history(repo, total=250, initial_samples=5)
    assert history[0]['stars'] == 1
    assert any(entry['stars'] >= 101 for entry in history)

    history_spread = updater._seed_star_history(repo, total=250, initial_samples=2)
    assert history_spread[0]['stars'] == 1

    class RepoErr(FakeRepo):
        def get_stargazers_with_dates(self):
            return FakeStargazers({0: [FakeStargazer('2026-01-01')]}, fail_page=0)

    warnings = []
    monkeypatch.setattr(updater.log, 'warning', lambda msg: warnings.append(msg))
    assert updater._seed_star_history(RepoErr(), total=100, initial_samples=5) == []
    assert warnings


def test_collect_star_history(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path / 'gh-pages'))

    fixed_today = datetime(2026, 3, 20, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_today

    monkeypatch.setattr(updater, 'datetime', FixedDatetime)

    repo = FakeRepo(name='demo', stars=10)
    monkeypatch.setattr(
        updater,
        '_seed_star_history',
        lambda r, total, initial_samples: [{'date': '2026-03-01', 'stars': 1}]
    )

    first = updater._collect_star_history(repo)
    assert first[-1] == {'date': '2026-03-20', 'stars': 10}

    cache = tmp_path / 'gh-pages' / 'github' / 'starHistory' / 'demo.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([{'date': '2026-03-20', 'stars': 5}]), encoding='utf-8')

    second = updater._collect_star_history(repo)
    assert second[-1]['stars'] == 10

    cache.write_text(json.dumps([{'date': '2026-03-19', 'stars': 5}]), encoding='utf-8')
    third = updater._collect_star_history(repo)
    assert third[-1] == {'date': '2026-03-20', 'stars': 10}

    cache.write_text('{bad', encoding='utf-8')
    fourth = updater._collect_star_history(repo)
    assert fourth[-1] == {'date': '2026-03-20', 'stars': 10}

    assert updater._collect_star_history(FakeRepo(stars=0)) == []


def test_process_github_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path / 'gh-pages'))

    writes = []
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))
    monkeypatch.setattr(
        updater.helpers,
        'save_image_from_url',
        lambda **kwargs: writes.append(('img', kwargs['file_path']))
    )
    monkeypatch.setattr(updater, '_get_stats_with_timeout', lambda repo: [FakeWeek(1, 1)])
    monkeypatch.setattr(updater, '_collect_star_history', lambda repo: [{'date': '2026-01-01', 'stars': 1}])
    monkeypatch.setattr(updater, '_fetch_code_scanning_alerts', lambda repo: [])
    monkeypatch.setattr(
        updater,
        '_build_code_scanning_history',
        lambda alerts: [{'date': '2026-01-01', 'open': 0}],
    )

    def post_ok(url, json, headers):
        return FakeResponse({'data': {'repository': {'openGraphImageUrl': 'https://example.com/image.png'}}})

    monkeypatch.setattr(updater.helpers.s, 'post', post_ok)

    updater._process_github_repo(FakeRepo(name='demo'), {'Authorization': 'x'}, 'https://api.github.com/graphql')

    assert any(path.endswith('languages\\demo') or path.endswith('languages/demo') for path, _ in writes)
    assert any(path.endswith('codeScanning\\demo') or path.endswith('codeScanning/demo') for path, _ in writes)
    assert any(
        path.endswith('codeScanningHistory\\demo') or path.endswith('codeScanningHistory/demo') for path, _ in writes)
    assert any(path == 'img' for path, _ in writes)


def test_process_github_repo_error_and_avatar_skip(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path / 'gh-pages'))
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda **kwargs: None)
    monkeypatch.setattr(updater, '_get_stats_with_timeout', lambda repo: None)
    monkeypatch.setattr(updater, '_collect_star_history', lambda repo: [])
    monkeypatch.setattr(updater, '_fetch_code_scanning_alerts', lambda repo: [])
    monkeypatch.setattr(updater, '_build_code_scanning_history', lambda alerts: [])

    monkeypatch.setattr(
        updater.helpers.s,
        'post',
        lambda url, json, headers: FakeResponse(
            {
                'data': {
                    'repository': {
                        'openGraphImageUrl': 'https://avatars.githubusercontent.com/u/1'
                    }
                }
            }
        )
    )
    updater._process_github_repo(FakeRepo(name='demo'), {'Authorization': 'x'}, 'https://api.github.com/graphql')

    monkeypatch.setattr(updater.helpers.s, 'post', lambda url, json, headers: FakeResponse({'bad': 1}))
    with pytest.raises(SystemExit):
        updater._process_github_repo(FakeRepo(name='demo'), {'Authorization': 'x'}, 'https://api.github.com/graphql')


def test_update_github(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'tok')
    monkeypatch.setenv('GITHUB_REPOSITORY_OWNER', 'owner')

    repo_active = FakeRepo('active', archived=False)
    repo_archived = FakeRepo('archived', archived=True)

    owner = SimpleNamespace(get_repos=lambda: [repo_active, repo_archived])

    class FakeGithub:
        def __init__(self, auth, timeout):
            self.per_page = None

        def get_user(self, name):
            return owner

    monkeypatch.setattr(updater, 'Github', FakeGithub)
    monkeypatch.setattr(updater.Auth, 'Token', lambda token: token)

    writes = []
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))
    processed = []
    monkeypatch.setattr(updater, '_process_github_repo', lambda repo, headers, graphql_url: processed.append(repo.name))
    monkeypatch.setattr(updater, 'BASE_DIR', 'base')

    updater.update_github()

    assert any(path.endswith('github\\repos') or path.endswith('github/repos') for path, _ in writes)
    assert processed == ['active']


def test_update_patreon(monkeypatch):
    monkeypatch.setenv('PATREON_CAMPAIGN_ID', '1')
    monkeypatch.setattr(updater, 'BASE_DIR', 'base')

    writes = []
    monkeypatch.setattr(updater.helpers.cs, 'get', lambda url: FakeResponse({'data': {'attributes': {'patrons': 10}}}))
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))

    updater.update_patreon()

    payload = next((data for _file_path, data in writes if 'patrons' in data), None)
    assert payload is not None
    assert payload['patrons'] == 10


def test_readthedocs_loop_and_update(monkeypatch, tmp_path):
    monkeypatch.setenv('READTHEDOCS_TOKEN', 'tok')
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path / 'gh-pages'))

    writes = []
    monkeypatch.setattr(updater.helpers, 'write_json_files', lambda file_path, data: writes.append((file_path, data)))

    responses = [
        FakeResponse({'results': [{'id': 1}], 'next': 'next'}),
        FakeResponse({'results': [{'id': 2}], 'next': None}),
    ]
    monkeypatch.setattr(updater.helpers.rtd_s, 'get', lambda url, headers: responses.pop(0))

    out = updater.readthedocs_loop('start', 'path')
    assert len(out) == 2

    bad = FakeResponse(raises=requests.exceptions.JSONDecodeError('x', 'y', 0))
    monkeypatch.setattr(updater.helpers.rtd_s, 'get', lambda url, headers: bad)
    assert updater.readthedocs_loop('start', 'path') == []

    monkeypatch.setattr(updater.helpers.rtd_s, 'get', lambda url, headers: FakeResponse({'foo': 'bar'}))
    assert updater.readthedocs_loop('start', 'path') == []

    project_data = [
        {
            'repository': {'url': 'https://github.com/LizardByte/demo.git'},
            '_links': {
                'builds': 'skip',
                'versions': 'v-url',
                'notifications': 'skip',
            },
        }
    ]
    monkeypatch.setattr(
        updater,
        'readthedocs_loop',
        lambda url, file_path: project_data if 'projects' in url else [{'v': 1}]
    )

    updater.update_readthedocs()


def test_append_thread_if_env_set_and_update(monkeypatch):
    threads = []
    monkeypatch.setenv('A', '1')
    updater.append_thread_if_env_set(['A'], 'name', lambda: None, threads)
    assert len(threads) == 1

    updater.append_thread_if_env_set(['MISSING'], 'x', lambda: None, threads)
    assert len(threads) == 1

    started = []
    joined = []

    class FakeThread:
        def __init__(self, name, target, kwargs=None):
            self.name = name
            self.target = target
            self.kwargs = kwargs or {}

        def start(self):
            started.append(self.name)

        def join(self):
            joined.append(self.name)

    monkeypatch.setattr(updater, 'Thread', FakeThread)

    monkeypatch.setenv('DASHBOARD_AUR_REPOS', 'a,b')
    monkeypatch.setenv('DISCORD_INVITE', 'x')
    monkeypatch.setenv('FACEBOOK_TOKEN', 'x')
    monkeypatch.setenv('FACEBOOK_PAGE_ID', 'x')
    monkeypatch.setenv('PATREON_CAMPAIGN_ID', 'x')
    monkeypatch.setenv('READTHEDOCS_TOKEN', 'x')
    monkeypatch.setenv('GITHUB_TOKEN', 'x')
    monkeypatch.setenv('GITHUB_REPOSITORY_OWNER', 'x')
    monkeypatch.setenv('CODECOV_TOKEN', 'x')
    monkeypatch.setenv('THREADING_EXCEPTION_HANDLER', '1')

    monkeypatch.setattr(updater.unhandled_exit, 'activate', lambda: started.append('activate'))
    monkeypatch.setattr(updater.unhandled_exit, 'deactivate', lambda: joined.append('deactivate'))

    updater.update()

    assert 'github' in started
    assert 'codecov' in started
    assert 'github' in joined
    assert 'codecov' in joined
    assert 'activate' in started
    assert 'deactivate' in joined


def test_fetch_code_scanning_alerts(monkeypatch):
    repo = FakeRepo(name='demo')

    class Alert:
        def __init__(self, state):
            self.state = state

    alerts = [Alert('open'), Alert('dismissed'), Alert('open')]
    monkeypatch.setattr(repo, 'get_codescan_alerts', lambda **kwargs: alerts)

    result = updater._fetch_code_scanning_alerts(repo)
    assert result == alerts


def test_fetch_code_scanning_alerts_404_skip(monkeypatch):
    repo = FakeRepo(name='demo')

    warnings = []
    monkeypatch.setattr(updater.log, 'warning', lambda msg: warnings.append(msg))

    def raise_404():
        raise GithubException(status=404, data={'message': 'no analysis found'})

    monkeypatch.setattr(repo, 'get_codescan_alerts', lambda **kwargs: raise_404())

    assert updater._fetch_code_scanning_alerts(repo) == []
    assert warnings


def test_build_code_scanning_history_empty():
    assert updater._build_code_scanning_history([]) == []


def test_build_code_scanning_history_no_timestamps():
    class Alert:
        created_at = None
        dismissed_at = None
        fixed_at = None

    assert updater._build_code_scanning_history([Alert()]) == []


def test_build_code_scanning_history_mixed_no_created_at():
    class Alert:
        def __init__(self, created, dismissed=None, fixed=None):
            self.created_at = (
                datetime.fromisoformat(created).replace(tzinfo=timezone.utc) if created else None
            )
            self.dismissed_at = (
                datetime.fromisoformat(dismissed).replace(tzinfo=timezone.utc) if dismissed else None
            )
            self.fixed_at = (
                datetime.fromisoformat(fixed).replace(tzinfo=timezone.utc) if fixed else None
            )

    alerts = [
        Alert(None),
        Alert('2026-03-01'),
    ]

    history = updater._build_code_scanning_history(alerts)
    assert history == [{'date': '2026-03-01', 'open': 1}]


def test_build_code_scanning_history():
    class Alert:
        def __init__(self, created, dismissed=None, fixed=None):
            self.created_at = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
            self.dismissed_at = (
                datetime.fromisoformat(dismissed).replace(tzinfo=timezone.utc) if dismissed else None
            )
            self.fixed_at = (
                datetime.fromisoformat(fixed).replace(tzinfo=timezone.utc) if fixed else None
            )

    alerts = [
        Alert('2026-01-01'),
        Alert('2026-01-02'),
        Alert('2026-01-03', dismissed='2026-01-05'),
        Alert('2026-01-04', fixed='2026-01-06'),
    ]

    history = updater._build_code_scanning_history(alerts)

    assert {'date': '2026-01-01', 'open': 1} in history
    assert {'date': '2026-01-02', 'open': 2} in history
    assert {'date': '2026-01-03', 'open': 3} in history
    assert {'date': '2026-01-04', 'open': 4} in history
    assert {'date': '2026-01-05', 'open': 3} in history
    assert {'date': '2026-01-06', 'open': 2} in history
    assert history == sorted(history, key=lambda x: x['date'])


def test_build_code_scanning_history_dismissed_and_fixed_same_day():
    class Alert:
        def __init__(self, created, dismissed=None, fixed=None):
            self.created_at = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
            self.dismissed_at = (
                datetime.fromisoformat(dismissed).replace(tzinfo=timezone.utc) if dismissed else None
            )
            self.fixed_at = (
                datetime.fromisoformat(fixed).replace(tzinfo=timezone.utc) if fixed else None
            )

    alerts = [
        Alert('2026-02-01', dismissed='2026-02-03'),
        Alert('2026-02-02', fixed='2026-02-03'),
    ]

    history = updater._build_code_scanning_history(alerts)

    assert {'date': '2026-02-01', 'open': 1} in history
    assert {'date': '2026-02-02', 'open': 2} in history
    assert {'date': '2026-02-03', 'open': 0} in history
