# standard imports
import json
from datetime import datetime, timezone

# lib imports
import pytest

# local imports
from src import builder


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def test_safe_name_uses_basename():
    assert builder._safe_name('x/y/z/repo') == 'repo'


def test_load_rtd_repos_handles_missing_and_errors(tmp_path):
    assert builder._load_rtd_repos(str(tmp_path)) == set()

    bad = tmp_path / 'readthedocs' / 'projects.json'
    bad.parent.mkdir(parents=True)
    bad.write_text('{bad', encoding='utf-8')
    assert builder._load_rtd_repos(str(tmp_path)) == set()


def test_load_rtd_repos_parses_repo_names(tmp_path):
    path = tmp_path / 'readthedocs' / 'projects.json'
    _write_json(path, [
        {'repository': {'url': 'https://github.com/LizardByte/demo.git'}},
        {'repository': {'url': 'https://github.com/LizardByte/demo2'}},
    ])
    assert builder._load_rtd_repos(str(tmp_path)) == {'demo', 'demo2'}


def test_get_coverage_and_history(tmp_path):
    _write_json(tmp_path / 'codecov' / 'demo.json', {'totals': {'coverage': 81.5}})
    _write_json(tmp_path / 'codecov' / 'demo_coverage_trend.json', [
        {'timestamp': '2026-01-01', 'avg': 70},
        {'timestamp': '2026-01-02', 'avg': None},
    ])

    assert builder._get_coverage(str(tmp_path), 'demo') == pytest.approx(81.5)
    assert builder._collect_coverage_history(str(tmp_path), 'demo') == [
        {'repo': 'demo', 'date': '2026-01-01', 'coverage': 70.0},
    ]


def test_get_coverage_and_history_fallbacks(tmp_path):
    assert builder._get_coverage(str(tmp_path), 'demo') == pytest.approx(0.0)
    assert builder._collect_coverage_history(str(tmp_path), 'demo') == []

    _write_json(tmp_path / 'codecov' / 'demo.json', {'totals': {'coverage': 'bad'}})
    _write_json(tmp_path / 'codecov' / 'demo_coverage_trend.json', {'bad': 1})
    assert builder._get_coverage(str(tmp_path), 'demo') == pytest.approx(0.0)
    assert builder._collect_coverage_history(str(tmp_path), 'demo') == []


def test_get_languages_prs_commit_activity_and_star_history(tmp_path):
    _write_json(tmp_path / 'github' / 'languages' / 'demo.json', {'Python': 100})
    _write_json(tmp_path / 'github' / 'pulls' / 'demo.json', [{'number': 1}])
    _write_json(tmp_path / 'github' / 'commitActivity' / 'demo.json', [
        {'week': 0, 'total': 0},
        {'week': 86400, 'total': 3},
    ])
    _write_json(tmp_path / 'github' / 'starHistory' / 'demo.json', [{'date': '2026-01-01', 'stars': 5}])

    assert builder._get_languages(str(tmp_path), 'demo') == {'Python': 100}
    assert builder._get_prs(str(tmp_path), 'demo') == [{'number': 1}]
    assert builder._get_commit_activity(str(tmp_path), 'demo') == [
        {'repo': 'demo', 'week': '1970-01-02', 'total': 3},
    ]
    assert builder._get_star_history(str(tmp_path), 'demo') == [
        {'repo': 'demo', 'date': '2026-01-01', 'stars': 5},
    ]


def test_get_languages_prs_commit_activity_and_star_history_fallbacks(tmp_path):
    assert builder._get_languages(str(tmp_path), 'demo') == {}
    assert builder._get_prs(str(tmp_path), 'demo') == []
    assert builder._get_commit_activity(str(tmp_path), 'demo') == []
    assert builder._get_star_history(str(tmp_path), 'demo') == []

    bad_lang = tmp_path / 'github' / 'languages' / 'demo.json'
    bad_prs = tmp_path / 'github' / 'pulls' / 'demo.json'
    bad_commits = tmp_path / 'github' / 'commitActivity' / 'demo.json'
    bad_stars = tmp_path / 'github' / 'starHistory' / 'demo.json'
    for path in [bad_lang, bad_prs, bad_commits, bad_stars]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{bad', encoding='utf-8')

    assert builder._get_languages(str(tmp_path), 'demo') == {}
    assert builder._get_prs(str(tmp_path), 'demo') == []
    assert builder._get_commit_activity(str(tmp_path), 'demo') == []
    assert builder._get_star_history(str(tmp_path), 'demo') == []


def test_get_code_scanning_open_and_history(tmp_path):
    _write_json(tmp_path / 'github' / 'codeScanning' / 'demo.json', {'open': 3})
    _write_json(tmp_path / 'github' / 'codeScanningHistory' / 'demo.json', [
        {'date': '2026-01-01', 'open': 1},
        {'date': '2026-01-02', 'open': 3},
    ])

    assert builder._get_code_scanning_open(str(tmp_path), 'demo') == 3
    assert builder._get_code_scanning_history(str(tmp_path), 'demo') == [
        {'repo': 'demo', 'date': '2026-01-01', 'open': 1},
        {'repo': 'demo', 'date': '2026-01-02', 'open': 3},
    ]


def test_get_code_scanning_open_and_history_fallbacks(tmp_path):
    assert builder._get_code_scanning_open(str(tmp_path), 'demo') == 0
    assert builder._get_code_scanning_history(str(tmp_path), 'demo') == []

    _write_json(tmp_path / 'github' / 'codeScanning' / 'demo.json', {'open': 'x'})
    _write_json(tmp_path / 'github' / 'codeScanningHistory' / 'demo.json', [{'bad': 1}])
    assert builder._get_code_scanning_open(str(tmp_path), 'demo') == 0
    assert builder._get_code_scanning_history(str(tmp_path), 'demo') == []

    _write_json(tmp_path / 'github' / 'codeScanningHistory' / 'demo.json', [{'date': '2026-01-01', 'open': 'bad'}])
    assert builder._get_code_scanning_history(str(tmp_path), 'demo') == []


def test_build_repo_entry_computes_counts_and_license():
    repo = {
        'name': 'demo',
        'stargazers_count': 9,
        'forks_count': 2,
        'open_issues_count': 3,
        'language': 'Python',
        'topics': ['a'],
        'created_at': 'x',
        'updated_at': 'y',
    }
    out = builder._build_repo_entry(repo, 80.0, {'Python': 100}, [{'id': 1}], {'demo'}, 4)

    assert out['issues'] == 2
    assert out['prs'] == 1
    assert out['license'] == 'No License'
    assert out['has_readthedocs'] is True
    assert out['code_scanning_open'] == 4

    repo['license'] = {'spdx_id': 'MIT'}
    assert builder._build_repo_entry(repo, 80.0, {}, [], set(), 0)['license'] == 'MIT'
    repo['license'] = {'name': 'Apache-2.0'}
    assert builder._build_repo_entry(repo, 80.0, {}, [], set(), 0)['license'] == 'Apache-2.0'


def test_build_end_to_end(monkeypatch, tmp_path):
    base = tmp_path / 'gh-pages'
    template = tmp_path / 'gh-pages-template'
    data_dir = template / 'assets' / 'data'

    repos = [
        {
            'name': 'demo',
            'private': False,
            'archived': False,
            'open_issues_count': 1,
            'stargazers_count': 4,
            'forks_count': 2,
            'language': 'Python',
            'topics': [],
            'created_at': 'a',
            'updated_at': 'b',
            'fork': False,
            'license': {'name': 'MIT'},
        },
        {'name': 'skip-private', 'private': True, 'archived': False},
        {'name': 'skip-archived', 'private': False, 'archived': True},
    ]
    _write_json(base / 'github' / 'repos.json', repos)
    _write_json(base / 'codecov' / 'demo.json', {'totals': {'coverage': 0}})
    _write_json(base / 'codecov' / 'demo_coverage_trend.json', [{'timestamp': '2026-01-03', 'avg': 91}])
    _write_json(base / 'github' / 'languages' / 'demo.json', {'Python': 100})
    _write_json(base / 'github' / 'pulls' / 'demo.json', [{'number': 7, 'title': 'PR'}])
    _write_json(base / 'github' / 'commitActivity' / 'demo.json', [{'week': 86400, 'total': 2}])
    _write_json(base / 'github' / 'starHistory' / 'demo.json', [{'date': '2026-01-01', 'stars': 4}])
    _write_json(base / 'github' / 'codeScanning' / 'demo.json', {'open': 5})
    _write_json(base / 'github' / 'codeScanningHistory' / 'demo.json', [{'date': '2026-01-04', 'open': 5}])
    _write_json(base / 'readthedocs' / 'projects.json', [
        {'repository': {'url': 'https://github.com/LizardByte/demo.git'}}])

    monkeypatch.setattr(builder, 'BASE_DIR', str(base))
    monkeypatch.setattr(builder, 'TEMPLATE_DIR', str(template))

    fixed_now = datetime(2026, 1, 5, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(builder, 'datetime', FixedDatetime)

    builder.build()

    built_repos = json.loads((data_dir / 'repos.json').read_text(encoding='utf-8'))
    assert len(built_repos) == 1
    assert built_repos[0]['coverage'] == pytest.approx(91.0)
    assert built_repos[0]['code_scanning_open'] == 5

    built_history = json.loads((data_dir / 'code_scanning_history.json').read_text(encoding='utf-8'))
    assert built_history == [{'repo': 'demo', 'date': '2026-01-04', 'open': 5}]

    metadata = json.loads((data_dir / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['repo_count'] == 1
    assert metadata['updated_at'] == fixed_now.isoformat()


def test_build_logs_error_when_repos_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, 'BASE_DIR', str(tmp_path / 'gh-pages'))
    monkeypatch.setattr(builder, 'TEMPLATE_DIR', str(tmp_path / 'gh-pages-template'))

    errors = []
    monkeypatch.setattr(builder.log, 'error', lambda msg: errors.append(msg))

    builder.build()

    assert errors
