"""Build consolidated dashboard data JSON files from raw collected data."""

# standard imports
import json
import os
from datetime import datetime, timezone

# local imports
from src import BASE_DIR, TEMPLATE_DIR
from src.logger import log


def _safe_name(name: str) -> str:
    return os.path.basename(name)


def _load_rtd_repos(base_dir: str) -> set:
    rtd_repos = set()
    rtd_path = os.path.join(base_dir, 'readthedocs', 'projects.json')
    if not os.path.exists(rtd_path):
        return rtd_repos
    try:
        with open(rtd_path) as f:
            rtd_data = json.load(f)
        for project in rtd_data:
            git_url = project.get('repository', {}).get('url', '')
            repo_name = git_url.rsplit('/', 1)[-1].rsplit('.git', 1)[0]
            rtd_repos.add(repo_name)
    except Exception:
        pass
    return rtd_repos


def _get_coverage(base_dir: str, name: str) -> float:
    safe = _safe_name(name)
    codecov_path = os.path.join(base_dir, 'codecov', f'{safe}.json')
    if not os.path.exists(codecov_path):
        return 0.0
    try:
        with open(codecov_path) as f:
            data = json.load(f)
        return float((data.get('totals') or {}).get('coverage', 0) or 0)
    except Exception:
        return 0.0


def _collect_coverage_history(base_dir: str, name: str) -> list:
    safe = _safe_name(name)
    trend_path = os.path.join(base_dir, 'codecov', f'{safe}_coverage_trend.json')
    if not os.path.exists(trend_path):
        return []
    try:
        with open(trend_path) as f:
            trend_data = json.load(f)
        return [
            {'repo': name, 'date': entry.get('timestamp'), 'coverage': float(entry['avg'])}
            for entry in trend_data
            if entry.get('avg') is not None
        ]
    except Exception:
        return []


def _get_languages(base_dir: str, name: str) -> dict:
    safe = _safe_name(name)
    lang_path = os.path.join(base_dir, 'github', 'languages', f'{safe}.json')
    if not os.path.exists(lang_path):
        return {}
    try:
        with open(lang_path) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_prs(base_dir: str, name: str) -> list:
    safe = _safe_name(name)
    pulls_path = os.path.join(base_dir, 'github', 'pulls', f'{safe}.json')
    if not os.path.exists(pulls_path):
        return []
    try:
        with open(pulls_path) as f:
            return json.load(f)
    except Exception:
        return []


def _get_commit_activity(base_dir: str, name: str) -> list:
    """
    Read cached commit activity for a repo and return flat weekly records.

    Parameters
    ----------
    base_dir : str
        Root directory containing the gh-pages data.
    name : str
        Repository name.

    Returns
    -------
    list
        List of dicts with keys ``repo``, ``week`` (ISO date), and ``total``.
    """
    safe = _safe_name(name)
    path = os.path.join(base_dir, 'github', 'commitActivity', f'{safe}.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        result = []
        for entry in data:
            week_ts = entry.get('week')
            total = entry.get('total', 0)
            if week_ts and total > 0:
                week_str = datetime.fromtimestamp(week_ts, tz=timezone.utc).strftime('%Y-%m-%d')
                result.append({'repo': name, 'week': week_str, 'total': total})
        return result
    except Exception:
        return []


def _build_repo_entry(repo: dict, coverage: float, languages: dict, prs: list, rtd_repos: set) -> dict:
    name = repo['name']
    pr_count = len(prs)
    open_issues_total = repo.get('open_issues_count', 0)
    issue_count = max(0, open_issues_total - pr_count)

    license_info = repo.get('license')
    license_name = 'No License'
    if license_info:
        license_name = license_info.get('name') or license_info.get('spdx_id') or 'No License'

    return {
        'name': name,
        'stars': repo.get('stargazers_count', 0),
        'forks': repo.get('forks_count', 0),
        'issues': issue_count,
        'prs': pr_count,
        'license': license_name,
        'coverage': coverage,
        'language': repo.get('language'),
        'languages': languages,
        'archived': repo.get('archived', False),
        'fork': repo.get('fork', False),
        'topics': repo.get('topics', []),
        'has_readthedocs': name in rtd_repos,
        'created_at': repo.get('created_at'),
        'updated_at': repo.get('updated_at'),
    }


def build():
    """
    Read raw data collected by updater.py and write dashboard-ready JSON files
    into gh-pages-template/assets/data/ for consumption by the Jekyll site's JavaScript.
    """
    log.info('Building dashboard data...')

    repos_path = os.path.join(BASE_DIR, 'github', 'repos.json')
    if not os.path.exists(repos_path):
        log.error(f'Repos file not found: {repos_path}')
        return

    with open(repos_path) as f:
        raw_repos = json.load(f)

    rtd_repos = _load_rtd_repos(BASE_DIR)
    repos = []
    prs_all = []
    coverage_history = []
    commit_activity = []

    for repo in raw_repos:
        if repo.get('private') or repo.get('archived'):
            continue

        name = repo['name']
        coverage = _get_coverage(BASE_DIR, name)
        coverage_hist = _collect_coverage_history(BASE_DIR, name)
        coverage_history.extend(coverage_hist)
        if not coverage and coverage_hist:
            coverage = max(coverage_hist, key=lambda e: e.get('date', '')).get('coverage', 0.0)
        languages = _get_languages(BASE_DIR, name)
        prs = _get_prs(BASE_DIR, name)
        commit_activity.extend(_get_commit_activity(BASE_DIR, name))

        repos.append(_build_repo_entry(repo, coverage, languages, prs, rtd_repos))
        prs_all.extend({'repo': name, **pr} for pr in prs)

    data_dir = os.path.join(TEMPLATE_DIR, 'assets', 'data')
    os.makedirs(data_dir, exist_ok=True)

    def write_json(filename, data):
        path = os.path.join(data_dir, filename)
        with open(path, 'w') as f:
            json.dump(data, f)
        log.info(f'Written: {path}')

    write_json('repos.json', repos)
    write_json('prs.json', prs_all)
    write_json('coverage_history.json', coverage_history)
    write_json('commit_activity.json', commit_activity)
    write_json('metadata.json', {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'repo_count': len(repos),
    })

    log.info('Dashboard build complete.')


if __name__ == '__main__':
    build()
