"""Organization discovery and persistent repository identities for public dashboard data."""

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from src import helpers
from src.logger import log

ORGANIZATIONS_URL = 'https://raw.githubusercontent.com/LizardByte/.github/master/organizations.json'
CATALOG_VERSION = 1


def read_json(path: str, default):
    """Read a restored cache without silently replacing malformed history."""
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def safe_key(value: str) -> str:
    """Accept a repository name or owner/name, never an arbitrary filesystem path."""
    parts = value.split('/')
    if len(parts) > 2 or any(not re.fullmatch(r'[\w.-]+', part) or part in {'.', '..'} for part in parts):
        raise ValueError('Invalid repository cache key')
    # Sanitize each filename component separately so owner/name keys stay distinct.
    name = os.path.basename(parts[-1])
    return os.path.join(os.path.basename(parts[0]), name) if len(parts) == 2 else name


def full_name(repo: dict) -> str:
    """Return the canonical GitHub identity, retaining support for legacy cached records."""
    return repo.get('full_name') or repo['name']


def github_name(url: str) -> str | None:
    """Resolve HTTPS/SSH Read the Docs repository URLs without matching by basename."""
    parsed = urlparse(url.replace('git@github.com:', 'https://github.com/'))
    name = parsed.path.strip('/').removesuffix('.git')
    if parsed.hostname != 'github.com' or name.count('/') != 1:
        return None
    safe_key(name)
    return name


def documentation_key(base_dir: str, url: str) -> str:
    """Find the persistent storage identity for a docs project, including pre-transfer aliases."""
    name = github_name(url)
    if name is None:
        raise ValueError('Read the Docs project has no GitHub repository identity')
    repos = read_json(os.path.join(base_dir, 'github', 'repos.json'), [])
    for repo in repos:
        aliases = [full_name(repo), *repo.get('dashboard_aliases', [])]
        if name.lower() in {alias.lower() for alias in aliases}:
            return cache_key(repo)
    return name


def cache_key(repository) -> str:
    """Return a stable storage key without relying on an owner's current repository name."""
    if isinstance(repository, dict):
        return repository.get('dashboard_key') or repository['name']
    key = getattr(repository, 'dashboard_key', None)
    return key if isinstance(key, str) else repository.name


def load_state(base_dir: str) -> tuple[dict, list]:
    """Restore the identity index, bootstrapping existing name-only URLs by repository ID."""
    state = read_json(os.path.join(base_dir, 'github', 'catalog.json'),
                      {'version': CATALOG_VERSION, 'repositories': {}, 'organizations': []})
    if state.get('version') != CATALOG_VERSION:
        raise ValueError('Unsupported dashboard catalog version')
    previous = read_json(os.path.join(base_dir, 'github', 'repos.json'), [])
    for repo in previous:
        if repo.get('private'):
            continue
        identity = str(repo['id'])
        state['repositories'].setdefault(identity, {
            'key': repo.get('dashboard_key') or repo['name'], 'full_name': full_name(repo),
        })
    keys = [safe_key(record['key']).casefold() for record in state['repositories'].values()]
    if len(keys) != len(set(keys)):
        raise ValueError('Repository caches have conflicting identities')
    return state, previous


def organizations(state: dict) -> tuple[list[str], bool]:
    """Fetch the shared registry, falling back only to a known prior registry on an outage."""
    try:
        response = helpers.s.get(url=ORGANIZATIONS_URL, timeout=30)
        if response.status_code != 200:
            raise ValueError('Organization registry request failed')
        names = response.json()
        if (not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not re.fullmatch(r'[a-z\d][a-z\d-]*', name, re.I)
                       for name in names)
                or len({name.lower() for name in names}) != len(names)):
            raise ValueError('Invalid organization registry')
        return names, True
    except Exception:
        cached = [org['name'] for org in state['organizations']]
        if not cached:
            raise
        log.warning('Organization registry unavailable; using the last successful registry.')
        return cached, False


def _register(state: dict, repo: dict) -> dict:
    identity = str(repo['id'])
    canonical = full_name(repo)
    safe_key(canonical)
    if identity not in state['repositories'] and any(
            record['key'].lower() == canonical.lower() for record in state['repositories'].values()):
        raise ValueError('Repository cache key belongs to another repository ID')
    record = state['repositories'].setdefault(identity, {'key': canonical, 'full_name': canonical})
    # Stable keys keep old public badge/banner paths valid across both transfers and renames.
    aliases = sorted({*record.get('aliases', []), record['full_name'], canonical})
    record.update({'full_name': canonical, 'aliases': aliases})
    return {**repo, 'dashboard_key': record['key'], 'dashboard_aliases': aliases,
            'dashboard_paths': {
                'banner': f'github/openGraphImages/{record["key"]}.png',
                'banner_small': f'github/openGraphImages/{record["key"]}_624x312.png',
                'coverage_badge': f'shields/codecov/{record["key"]}.json',
            }}


def discover(github, base_dir: str) -> tuple[list, dict]:
    """Collect each org independently, retaining failed-org snapshots and deduplicating by GitHub ID."""
    state, previous = load_state(base_dir)
    names, registry_fresh = organizations(state)
    collected_at = datetime.now(timezone.utc).isoformat()
    snapshots, fresh, statuses = {}, {}, []
    for name in names:
        try:
            repos = list(github.get_organization(name).get_repos(type='public'))
            public = {str(repo.raw_data['id']): repo for repo in repos if not repo.raw_data.get('private')}
            fresh.update(public)
            statuses.append({'name': name, 'status': 'ok', 'collected_at': collected_at})
        except Exception:
            cached = [repo for repo in previous if full_name(repo).split('/')[0].lower() == name.lower()
                      and not repo.get('private')]
            snapshots.update((str(repo['id']), repo) for repo in cached)
            prior = next((org for org in state['organizations'] if org['name'].lower() == name.lower()), {})
            statuses.append({**prior, 'name': name, 'status': 'stale' if cached else 'unavailable'})
            log.warning(f'GitHub organization {name} unavailable; preserving its cached public inventory.')
    snapshots.update((identity, repo.raw_data) for identity, repo in fresh.items())
    records = [_register(state, data) for data in snapshots.values()]
    records.sort(key=lambda repo: full_name(repo).lower())
    for identity, repo in fresh.items():
        repo.dashboard_key = state['repositories'][identity]['key']
    state.update({'source_url': ORGANIZATIONS_URL, 'registry_fresh': registry_fresh,
                  'organizations': statuses})
    helpers.write_json_files(os.path.join(base_dir, 'github', 'catalog'), state)
    helpers.write_json_files(os.path.join(base_dir, 'github', 'repos'), records)
    return list(fresh.values()), state
