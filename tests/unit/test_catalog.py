"""Repository transfers must preserve published paths and history without conflating same-name projects."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from src import catalog, builder, updater


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def repository(identity, full_name, **values):
    return SimpleNamespace(name=full_name.split('/')[1], owner=SimpleNamespace(login=full_name.split('/')[0]),
                           raw_data={'id': identity, 'name': full_name.split('/')[1], 'full_name': full_name,
                                     'private': False, 'archived': False, **values})


def client(groups):
    def organization(name):
        def repos(**kwargs):
            assert kwargs == {'type': 'public'}
            if isinstance(groups[name], Exception):
                raise groups[name]
            return iter(groups[name])
        return SimpleNamespace(get_repos=repos)
    return SimpleNamespace(get_organization=organization)


def registry(monkeypatch, names, status=200):
    def get(**kwargs):
        assert kwargs == {'url': catalog.ORGANIZATIONS_URL, 'timeout': 30}
        return SimpleNamespace(status_code=status, json=lambda: names)
    monkeypatch.setattr(catalog.helpers.s, 'get', get)


def test_transfer_keeps_original_urls_and_cache_bytes(monkeypatch, tmp_path):
    old = repository(17, 'LizardByte/Sunshine').raw_data
    write(tmp_path / 'github/repos.json', [old])
    histories = ['commitActivity', 'commitActivityHashes', 'starHistory', 'codeScanningHistory', 'prMetrics']
    for kind in histories:
        write(tmp_path / 'github' / kind / 'Sunshine.json', [{'historical': kind}])
    banner = tmp_path / 'github/openGraphImages/Sunshine.png'
    banner.parent.mkdir(parents=True)
    banner.write_bytes(b'historical-banner')
    write(tmp_path / 'shields/codecov/Sunshine.json', {'message': '97%'})
    paths = [path for path in tmp_path.rglob('*') if path.is_file() and path.name != 'repos.json']
    checksums = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    registry(monkeypatch, ['LizardByte', 'SunshineStream'])
    moved = repository(17, 'SunshineStream/Sunshine')
    _, state = catalog.discover(client({'LizardByte': [], 'SunshineStream': [moved]}), str(tmp_path))
    assert state['repositories']['17']['key'] == 'Sunshine'
    assert catalog.cache_key(moved) == 'Sunshine'
    data = json.loads((tmp_path / 'github/repos.json').read_text(encoding='utf-8'))[0]
    assert data['dashboard_paths']['banner'] == 'github/openGraphImages/Sunshine.png'
    assert data['dashboard_paths']['coverage_badge'] == 'shields/codecov/Sunshine.json'
    assert data['full_name'] == 'SunshineStream/Sunshine'
    assert data['dashboard_aliases'] == ['LizardByte/Sunshine', 'SunshineStream/Sunshine']
    assert checksums == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert catalog.documentation_key(str(tmp_path), 'git@github.com:LizardByte/Sunshine.git') == 'Sunshine'
    # A later collection must retain the same identity without depending on the original catalog location.
    catalog.discover(client({'LizardByte': [], 'SunshineStream': [moved]}), str(tmp_path))
    assert catalog.cache_key(moved) == 'Sunshine'


def test_distinct_orgs_with_same_names_keep_independent_storage_and_private_repos_stay_private(monkeypatch, tmp_path):
    registry(monkeypatch, ['First', 'Second'])
    first, second = repository(1, 'First/demo'), repository(2, 'Second/demo', archived=True)
    private = repository(3, 'Second/private', private=True, description='never publish this')
    objects, state = catalog.discover(client({'First': [first], 'Second': [second, private]}), str(tmp_path))
    assert len(objects) == 2
    assert catalog.cache_key(first) == 'First/demo'
    assert catalog.cache_key(second) == 'Second/demo'
    assert catalog.cache_key({'name': 'old'}) == 'old'
    serialized = (tmp_path / 'github/repos.json').read_text(encoding='utf-8')
    assert 'never publish' not in serialized
    assert '3' not in state['repositories']
    assert json.loads(serialized)[1]['archived']


def test_failed_org_preserves_snapshot_but_fresh_owner_wins_for_transferred_id(monkeypatch, tmp_path):
    write(tmp_path / 'github/repos.json', [
        repository(1, 'First/moved').raw_data, repository(2, 'First/stays').raw_data])
    write(tmp_path / 'github/catalog.json', {'version': 1, 'repositories': {}, 'organizations': [
        {'name': 'First', 'status': 'ok', 'collected_at': 'original-time'}]})
    registry(monkeypatch, ['First', 'Second', 'Empty'])
    fresh = repository(1, 'Second/moved')
    _, state = catalog.discover(client({'First': RuntimeError('offline'), 'Second': [fresh],
                                       'Empty': RuntimeError('no access')}), str(tmp_path))
    records = json.loads((tmp_path / 'github/repos.json').read_text(encoding='utf-8'))
    assert {r['id']: r['full_name'] for r in records} == {1: 'Second/moved', 2: 'First/stays'}
    assert state['organizations'][0] == {'name': 'First', 'status': 'stale', 'collected_at': 'original-time'}
    assert state['organizations'][2]['status'] == 'unavailable'


@pytest.mark.parametrize('names', [[], {}, ['bad/name'], ['First', 'first'], [1]])
def test_invalid_registry_cannot_replace_valid_state(monkeypatch, names):
    registry(monkeypatch, names)
    with pytest.raises(ValueError):
        catalog.organizations({'organizations': []})
    assert catalog.organizations({'organizations': [{'name': 'Previous'}]}) == (['Previous'], False)


def test_registry_outage_uses_last_registry_only_if_available(monkeypatch):
    registry(monkeypatch, [], status=503)
    assert catalog.organizations({'organizations': [{'name': 'Cached'}]}) == (['Cached'], False)
    with pytest.raises(ValueError, match='request failed'):
        catalog.organizations({'organizations': []})


@pytest.mark.parametrize('key', ['', '.', '..', '../repo', '/repo', 'a/b/c', r'..\repo'])
def test_invalid_keys_cannot_escape_cache_root(key):
    with pytest.raises(ValueError, match='cache key'):
        catalog.safe_key(key)


def test_index_validation_rejects_ambiguous_legacy_paths_and_unknown_versions(tmp_path):
    write(tmp_path / 'github/repos.json', [
        repository(1, 'First/demo').raw_data, repository(2, 'Second/demo').raw_data])
    with pytest.raises(ValueError, match='conflicting identities'):
        catalog.load_state(str(tmp_path))
    write(tmp_path / 'github/catalog.json', {'version': 2})
    with pytest.raises(ValueError, match='Unsupported'):
        catalog.load_state(str(tmp_path))
    conflicting = repository(2, 'First/demo').raw_data
    with pytest.raises(ValueError, match='another repository ID'):
        catalog._register({'repositories': {'1': {'key': 'First/demo'}}}, conflicting)


def test_legacy_private_entries_are_not_registered_and_non_github_docs_are_not_guessed(tmp_path):
    write(tmp_path / 'github/repos.json', [repository(1, 'First/private', private=True).raw_data])
    state, _ = catalog.load_state(str(tmp_path))
    assert state['repositories'] == {}
    assert catalog.github_name('https://example.com/First/demo.git') is None
    assert catalog.documentation_key(str(tmp_path), 'https://github.com/Other/demo.git') == 'Other/demo'
    with pytest.raises(ValueError, match='no GitHub'):
        catalog.documentation_key(str(tmp_path), 'https://example.com/First/demo.git')


def test_builder_reads_distinct_keys_and_publishes_canonical_links_and_legacy_report_alias(monkeypatch, tmp_path):
    base, template = tmp_path / 'cache', tmp_path / 'site'
    records = [dict(repository(1, 'New/demo').raw_data, dashboard_key='demo', dashboard_aliases=['Old/demo']),
               dict(repository(2, 'Other/demo').raw_data, dashboard_key='Other/demo')]
    write(base / 'github/repos.json', records)
    write(base / 'github/languages/demo.json', {'Python': 7})
    write(base / 'github/languages/Other/demo.json', {'Rust': 9})
    write(base / 'github/pulls/demo.json', [{'number': 1, 'title': 'first'}])
    write(base / 'github/pulls/Other/demo.json', [{'number': 1, 'title': 'second'}])
    write(base / 'github/starHistory/demo.json', [{'date': '2025-01-01', 'stars': 11}])
    write(base / 'readthedocs/projects.json', [{'repository': {'url': 'https://github.com/Old/demo.git'}}])
    monkeypatch.setattr(builder, 'BASE_DIR', str(base))
    monkeypatch.setattr(builder, 'TEMPLATE_DIR', str(template))
    builder.build()
    data = json.loads((template / 'assets/data/repos.json').read_text(encoding='utf-8'))
    assert [(repo['name'], repo['languages'], repo['has_readthedocs']) for repo in data] == [
        ('New/demo', {'Python': 7}, True), ('Other/demo', {'Rust': 9}, False)]
    pulls = json.loads((template / 'assets/data/prs.json').read_text(encoding='utf-8'))
    assert [(pull['repo'], pull['number']) for pull in pulls] == [('New/demo', 1), ('Other/demo', 1)]
    assert json.loads((template / 'assets/data/star_history.json').read_text(encoding='utf-8'))[0]['repo'] == 'New/demo'
    assert 'permalink: /pr-metrics/demo/' in (template / 'pr-metrics/demo.md').read_text(encoding='utf-8')
    assert 'repo%3ANew%2Fdemo' in (template / 'pr-metrics/New/demo.md').read_text(encoding='utf-8')
    index = (template / 'pr-metrics/index.md').read_text(encoding='utf-8')
    assert 'org%3ANew+org%3AOther' in index


def test_transferred_star_and_commit_caches_still_resolve_under_original_key(monkeypatch, tmp_path):
    repo = repository(17, 'New/renamed')
    repo.dashboard_key = 'old-name'
    repo.stargazers_count = 9
    write(tmp_path / 'github/starHistory/old-name.json', [{'date': '2024-01-01', 'stars': 5}])
    write(tmp_path / 'github/commitActivityHashes/old-name.json', {'sha': 'unchanged'})
    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path))
    assert updater._collect_star_history(repo)[0] == {'date': '2024-01-01', 'stars': 5}
    assert updater._cached_commit_activity_sha(repo) == 'unchanged'


def test_coverage_transfer_keeps_old_points_and_updates_overlaps(tmp_path):
    path = tmp_path / 'codecov/old-name_coverage_trend'
    write(path.with_suffix('.json'), [
        {'timestamp': '2024-01-01', 'avg': 70}, {'timestamp': '2025-01-01', 'avg': 80}])
    assert updater._merge_coverage_history(str(path), [
        {'timestamp': '2025-01-01', 'avg': 85}, {'timestamp': '2026-01-01', 'avg': 90}]) == [
        {'timestamp': '2024-01-01', 'avg': 70}, {'timestamp': '2025-01-01', 'avg': 85},
        {'timestamp': '2026-01-01', 'avg': 90}]


def test_codecov_each_org_uses_its_owner_and_stable_key_and_retains_failed_data(monkeypatch, tmp_path):
    records = [dict(repository(1, 'New/demo').raw_data, dashboard_key='legacy-demo'),
               dict(repository(2, 'Other/demo').raw_data, dashboard_key='Other/demo'),
               dict(repository(3, 'New/archived', archived=True).raw_data, dashboard_key='old-archived')]
    write(tmp_path / 'github/repos.json', records)
    write(tmp_path / 'github/catalog.json', {'organizations': [{'name': 'New'}, {'name': 'Other'}, {'name': 'Failed'}]})
    write(tmp_path / 'codecov/Other/demo.json', {'totals': {'coverage': 71}})
    write(tmp_path / 'codecov/legacy-demo_coverage_trend.json', [{'timestamp': '2024-01-01', 'avg': 70}])
    calls = []

    def get(url, headers):
        calls.append(url)
        status = 200
        if '/Failed/' in url:
            data, status = {}, 403
        elif url.endswith('/repos?page_size=500'):
            data = {'next': None, 'results': [{'name': 'demo'}, {'name': 'private'}, {'name': 'archived'}]}
        elif '/Other/' in url:
            data, status = {}, 404
        else:
            data = {'totals': {'coverage': 92}}
        return SimpleNamespace(status_code=status, json=lambda: data)

    monkeypatch.setattr(updater, 'BASE_DIR', str(tmp_path))
    monkeypatch.setenv('CODECOV_TOKEN', 'test-only')
    monkeypatch.setattr(updater.helpers.s, 'get', get)
    monkeypatch.setattr(updater, 'fetch_coverage_trend_for_repo',
                        lambda *args: [{'timestamp': '2026-01-01', 'avg': 92}])
    updater.update_codecov()
    assert json.loads((tmp_path / 'shields/codecov/legacy-demo.json').read_text())['message'] == '92%'
    assert json.loads((tmp_path / 'codecov/Other/demo.json').read_text())['totals']['coverage'] == 71
    assert json.loads((tmp_path / 'codecov/legacy-demo_coverage_trend.json').read_text()) == [
        {'timestamp': '2024-01-01', 'avg': 70}, {'timestamp': '2026-01-01', 'avg': 92}]
    state = json.loads((tmp_path / 'github/catalog.json').read_text())
    assert [org['codecov_status'] for org in state['organizations']] == ['ok', 'stale', 'stale']
    assert not any(url.endswith('/private') or url.endswith('/archived') for url in calls)


def test_codecov_repository_pagination_stays_inside_the_source_org(monkeypatch):
    base = 'https://codecov.io/api/v2/github/First'
    responses = iter([{'next': base + '/repos?page=2', 'results': [{'name': 'one'}]},
                      {'next': None, 'results': [{'name': 'two'}]}])
    monkeypatch.setattr(updater.helpers.s, 'get',
                        lambda **kwargs: SimpleNamespace(status_code=200, json=lambda: next(responses)))
    assert list(updater._codecov_repositories(base, {})) == [{'name': 'one'}, {'name': 'two'}]
