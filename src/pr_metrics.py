"""Collect, calculate, and render pull-request metrics."""

# standard imports
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.parse import quote_plus

# local imports
from src import helpers

GRAPHQL_URL = 'https://api.github.com/graphql'
GITHUB_OWNER = 'LizardByte'
CACHE_VERSION = 2
HISTORY_DAYS = 365
REPORT_DAYS = 90
CACHE_MAX_AGE = timedelta(hours=24)
STALE_DAYS = 30
SEARCH_OPEN = 'is:open'
SEARCH_READY = '-is:draft'
SEARCH_DRAFT = 'is:draft'
SEARCH_MERGED = 'is:merged'
SEARCH_NO_REVIEW = 'review:none'
SORTABLE_TABLE = '{: .pr-metrics-sortable}'
REACTION_EMOJI = {
    'THUMBS_UP': '👍',
    'THUMBS_DOWN': '👎',
    'LAUGH': '😄',
    'HOORAY': '🎉',
    'CONFUSED': '😕',
    'HEART': '❤️',
    'ROCKET': '🚀',
    'EYES': '👀',
}

PULL_REQUEST_QUERY = """
query($owner: String!, $name: String!, $states: [PullRequestState!], $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 100
      after: $cursor
      states: $states
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        number
        title
        url
        state
        isDraft
        createdAt
        updatedAt
        closedAt
        mergedAt
        additions
        deletions
        changedFiles
        reviewDecision
        author { login }
        reviews(
          first: 1
          states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]
        ) {
          totalCount
          nodes {
            submittedAt
          }
        }
        approvals: reviews(first: 1, states: [APPROVED]) {
          nodes { submittedAt }
        }
        reactionGroups {
          content
          reactors(first: 1) { totalCount }
        }
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse a GitHub timestamp into an aware datetime."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _isoformat(value: datetime | None) -> str | None:
    """Return a UTC ISO timestamp when a datetime is present."""
    return value.astimezone(timezone.utc).isoformat() if value else None


def _window_cutoff(now: datetime, days: int) -> datetime:
    """Return a whole-second UTC cutoff shared by calculations and search links."""
    return (now.astimezone(timezone.utc) - timedelta(days=days)).replace(microsecond=0)


def _search_timestamp(value: datetime) -> str:
    """Format a datetime for an exact GitHub search qualifier."""
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def is_active_repo(repo: dict) -> bool:
    """Return whether a repository belongs in the public dashboard metrics."""
    topics = repo.get('topics') or []
    return not repo.get('private') and not repo.get('archived') and 'package-manager' not in topics


def cache_path(base_dir: str, repository: str) -> str:
    """Return the cache path without its JSON extension."""
    return os.path.join(base_dir, 'github', 'prMetrics', os.path.basename(repository))


def load_cache(base_dir: str, repository: str) -> dict | None:
    """Load a valid repository metrics cache, returning ``None`` on failure."""
    try:
        with open(f'{cache_path(base_dir, repository)}.json') as cache_file:
            cache = json.load(cache_file)
        if isinstance(cache, dict) and isinstance(cache.get('pull_requests'), list):
            return cache
    except Exception:
        pass
    return None


def cache_is_fresh(cache: dict | None, now: datetime) -> bool:
    """Return whether a cache has the current schema window and is less than one day old."""
    if (
            not cache
            or cache.get('cache_version') != CACHE_VERSION
            or cache.get('history_days') != HISTORY_DAYS
    ):
        return False
    try:
        collected_at = _parse_datetime(cache.get('collected_at'))
        age = now - collected_at
        return timedelta(0) <= age < CACHE_MAX_AGE
    except Exception:
        return False


def _graphql_connection(session, headers: dict, variables: dict) -> dict:
    """Request one pull-request connection page from GitHub GraphQL."""
    response = session.post(
        url=GRAPHQL_URL,
        json={'query': PULL_REQUEST_QUERY, 'variables': variables},
        headers=headers,
    )
    try:
        payload = response.json()
    except Exception as error:
        raise RuntimeError(f'Invalid GitHub GraphQL response: {response.text}') from error

    if response.status_code != 200 or payload.get('errors'):
        detail = payload.get('errors') or payload
        raise RuntimeError(f'GitHub GraphQL request failed: {detail}')

    repository = (payload.get('data') or {}).get('repository')
    if repository is None:
        raise RuntimeError('GitHub GraphQL response did not include the repository')
    return repository['pullRequests']


def _normalize_pull(repository: str, pull: dict) -> dict:
    """Convert a GraphQL pull-request node into the stable cache schema."""
    review_times = _review_timestamps(pull.get('reviews'))
    approval_times = _review_timestamps(pull.get('approvals'))
    reactions = _reaction_counts(pull.get('reactionGroups'))
    author = pull.get('author') or {}

    return {
        'repository': repository,
        'number': pull['number'],
        'title': pull.get('title') or '',
        'url': pull.get('url') or '',
        'state': (pull.get('state') or '').lower(),
        'draft': bool(pull.get('isDraft')),
        'author': author.get('login'),
        'created_at': pull.get('createdAt'),
        'updated_at': pull.get('updatedAt'),
        'closed_at': pull.get('closedAt'),
        'merged_at': pull.get('mergedAt'),
        'additions': int(pull.get('additions') or 0),
        'deletions': int(pull.get('deletions') or 0),
        'changed_files': int(pull.get('changedFiles') or 0),
        'review_count': int((pull.get('reviews') or {}).get('totalCount') or 0),
        'review_decision': pull.get('reviewDecision'),
        'first_review_at': _isoformat(review_times[0]) if review_times else None,
        'first_approval_at': _isoformat(approval_times[0]) if approval_times else None,
        'reactions': reactions,
    }


def _review_timestamps(connection: dict | None) -> list[datetime]:
    """Return sorted submitted timestamps from a GraphQL review connection."""
    timestamps = [
        _parse_datetime(review.get('submittedAt'))
        for review in (connection or {}).get('nodes') or []
        if review.get('submittedAt')
    ]
    return sorted(timestamp for timestamp in timestamps if timestamp is not None)


def _reaction_counts(groups: list[dict] | None) -> list[dict]:
    """Normalize non-zero GraphQL reaction groups."""
    reactions = []
    for group in groups or []:
        count = int((group.get('reactors') or {}).get('totalCount') or 0)
        if group.get('content') and count:
            reactions.append({'content': group['content'], 'count': count})
    return reactions


def _fetch_connection(
        repository,
        headers: dict,
        session,
        states: list[str],
        cutoff: datetime | None = None,
) -> list[dict]:
    """Fetch and normalize one state group, stopping once updated records cross the cutoff."""
    cursor = None
    pulls = []

    while True:
        connection = _graphql_connection(session, headers, {
            'owner': repository.owner.login,
            'name': repository.name,
            'states': states,
            'cursor': cursor,
        })
        reached_cutoff = False
        for pull in connection.get('nodes') or []:
            updated_at = _parse_datetime(pull.get('updatedAt'))
            if cutoff and updated_at and updated_at < cutoff:
                reached_cutoff = True
                break
            pulls.append(_normalize_pull(repository.name, pull))

        page_info = connection.get('pageInfo') or {}
        if reached_cutoff or not page_info.get('hasNextPage'):
            break
        cursor = page_info.get('endCursor')
        if not cursor:
            raise RuntimeError('GitHub GraphQL pagination did not return an end cursor')

    return pulls


def fetch_repository(repository, headers: dict, session, now: datetime) -> list[dict]:
    """Fetch every open PR and one year of recently updated completed PRs."""
    cutoff = now - timedelta(days=HISTORY_DAYS)
    pulls = _fetch_connection(repository, headers, session, ['OPEN'])
    pulls.extend(_fetch_connection(repository, headers, session, ['CLOSED', 'MERGED'], cutoff))
    unique = {pull['number']: pull for pull in pulls}
    return sorted(unique.values(), key=lambda pull: pull.get('updated_at') or '', reverse=True)


def refresh_repository(repository, base_dir: str, headers: dict, session, now: datetime | None = None) -> bool:
    """Refresh one repository cache when stale, returning whether it was written."""
    now = now or datetime.now(tz=timezone.utc)
    existing = load_cache(base_dir, repository.name)
    if cache_is_fresh(existing, now):
        return False

    pulls = fetch_repository(repository, headers, session, now)
    helpers.write_json_files(
        file_path=cache_path(base_dir, repository.name),
        data={
            'repository': repository.name,
            'collected_at': now.isoformat(),
            'cache_version': CACHE_VERSION,
            'history_days': HISTORY_DAYS,
            'pull_requests': pulls,
        },
    )
    return True


def _hours_between(start: str | None, end: str | None) -> float | None:
    """Return elapsed hours between two timestamps."""
    start_time = _parse_datetime(start)
    end_time = _parse_datetime(end)
    if not start_time or not end_time:
        return None
    return (end_time - start_time).total_seconds() / 3600


def _percentile(values: list[float], percentile: float) -> float | None:
    """Return a linearly interpolated percentile."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _on_or_after(timestamp: str | None, cutoff: datetime) -> bool:
    """Return whether a timestamp is present and on or after a cutoff."""
    parsed = _parse_datetime(timestamp)
    return parsed is not None and parsed >= cutoff


def _elapsed_values(pulls: list[dict], end_field: str) -> list[float]:
    """Return elapsed hours from PR creation to a selected event."""
    elapsed_values = []
    for pull in pulls:
        elapsed = _hours_between(pull.get('created_at'), pull.get(end_field))
        if elapsed is not None:
            elapsed_values.append(elapsed)
    return elapsed_values


def _pending_pulls(ready_pulls: list[dict], now: datetime) -> list[dict]:
    """Add age and inactivity values to ready pull requests."""
    pending = []
    ordered = sorted(
        ready_pulls,
        key=lambda item: (item.get('created_at') is None, item.get('created_at') or ''),
    )
    for pull in ordered:
        created_at = _parse_datetime(pull.get('created_at'))
        updated_at = _parse_datetime(pull.get('updated_at'))
        pending.append({
            **pull,
            'age_days': (now - created_at).days if created_at else 0,
            'inactive_days': (now - updated_at).days if updated_at else 0,
        })
    return pending


def _is_not_reviewed(pull: dict) -> bool:
    """Match GitHub's ``review:none`` current review-decision bucket."""
    return pull.get('review_decision') is None


def _is_awaiting_approval(pull: dict) -> bool:
    """Return whether a ready PR has review activity but no current decision."""
    decision = pull.get('review_decision')
    return decision == 'REVIEW_REQUIRED' or (decision is None and bool(pull.get('review_count')))


def calculate(pulls: list[dict], now: datetime, days: int = REPORT_DAYS) -> dict:
    """Calculate current-backlog and completed-PR metrics."""
    cutoff = _window_cutoff(now, days)
    stale_cutoff = _window_cutoff(now, STALE_DAYS)
    open_pulls = [pull for pull in pulls if pull.get('state') == 'open']
    ready_pulls = [pull for pull in open_pulls if not pull.get('draft')]
    merged_pulls = [pull for pull in pulls if _on_or_after(pull.get('merged_at'), cutoff)]
    closed_unmerged = [
        pull for pull in pulls
        if pull.get('state') == 'closed'
        and not pull.get('merged_at')
        and _on_or_after(pull.get('closed_at'), cutoff)
    ]
    opened_pulls = [pull for pull in pulls if _on_or_after(pull.get('created_at'), cutoff)]

    merge_hours = _elapsed_values(merged_pulls, 'merged_at')
    review_hours = _elapsed_values(merged_pulls, 'first_review_at')
    approval_hours = _elapsed_values(merged_pulls, 'first_approval_at')
    completed_count = len(merged_pulls) + len(closed_unmerged)

    return {
        'days': days,
        'open': len(open_pulls),
        'draft': len(open_pulls) - len(ready_pulls),
        'ready': len(ready_pulls),
        'not_reviewed': sum(1 for pull in ready_pulls if _is_not_reviewed(pull)),
        'awaiting_approval': sum(1 for pull in ready_pulls if _is_awaiting_approval(pull)),
        'changes_requested': sum(
            1 for pull in ready_pulls if pull.get('review_decision') == 'CHANGES_REQUESTED'
        ),
        'stale': sum(
            1 for pull in open_pulls
            if (_parse_datetime(pull.get('updated_at')) or now) < stale_cutoff
        ),
        'opened': len(opened_pulls),
        'merged': len(merged_pulls),
        'closed_unmerged': len(closed_unmerged),
        'merge_rate': (len(merged_pulls) / completed_count * 100) if completed_count else None,
        'median_first_review_hours': median(review_hours) if review_hours else None,
        'p75_first_review_hours': _percentile(review_hours, 0.75),
        'median_first_approval_hours': median(approval_hours) if approval_hours else None,
        'p75_first_approval_hours': _percentile(approval_hours, 0.75),
        'median_merge_hours': median(merge_hours) if merge_hours else None,
        'p75_merge_hours': _percentile(merge_hours, 0.75),
        'merged_without_review': sum(1 for pull in merged_pulls if not pull.get('review_count')),
        'merged_without_approval': sum(1 for pull in merged_pulls if not pull.get('first_approval_at')),
        'additions': sum(int(pull.get('additions') or 0) for pull in merged_pulls),
        'deletions': sum(int(pull.get('deletions') or 0) for pull in merged_pulls),
        'pending': _pending_pulls(ready_pulls, now),
    }


def _format_duration(hours: float | None) -> str:
    """Format an elapsed-hour metric for a report table."""
    if hours is None:
        return '—'
    if hours < 24:
        return f'{hours:.1f} h'
    return f'{hours / 24:.1f} d'


def _format_percent(value: float | None) -> str:
    """Format a percentage metric for a report table."""
    return '—' if value is None else f'{value:.1f}%'


def _markdown_text(value: object) -> str:
    """Escape text for use inside a Markdown table cell."""
    return (
        str(value or '')
        .replace('\\', '\\\\')
        .replace('[', '\\[')
        .replace(']', '\\]')
        .replace('\n', ' ')
        .replace('|', '\\|')
    )


def _github_search(repository: str | None, qualifiers: list[str]) -> str:
    """Build a GitHub pull-request search URL for an organization or repository."""
    scope = f'repo:{GITHUB_OWNER}/{repository}' if repository else f'org:{GITHUB_OWNER}'
    query = ' '.join(['is:pr', scope, *qualifiers])
    return f'https://github.com/pulls?q={quote_plus(query)}'


def _metric_link(value: int, repository: str | None, *qualifiers: str) -> str:
    """Link a displayed metric count to the closest matching GitHub search."""
    return f'[{value}]({_github_search(repository, list(qualifiers))})'


def _format_reactions(reactions: list[dict] | None) -> str:
    """Format reaction groups as a sortable total followed by emoji counts."""
    groups = [reaction for reaction in (reactions or []) if reaction.get('count')]
    groups.sort(key=lambda reaction: list(REACTION_EMOJI).index(reaction['content'])
                if reaction.get('content') in REACTION_EMOJI else len(REACTION_EMOJI))
    total = sum(int(reaction['count']) for reaction in groups)
    if not groups:
        return '0'
    details = ' '.join(
        f"{REACTION_EMOJI.get(reaction['content'], reaction['content'])} {reaction['count']}"
        for reaction in groups
    )
    return f'{total} — {details}'


def _format_collected_at(collected_at: str | None) -> str:
    """Format a collection timestamp for display, tolerating invalid cache data."""
    if not collected_at:
        return 'unavailable'
    try:
        return _parse_datetime(collected_at).strftime('%Y-%m-%d %H:%M UTC')
    except (AttributeError, ValueError):
        return 'unavailable'


def _review_status(pull: dict) -> str:
    """Return the display status for a ready pull request."""
    decision = pull.get('review_decision')
    if decision == 'CHANGES_REQUESTED':
        return 'Changes requested'
    if decision == 'APPROVED' or pull.get('first_approval_at'):
        return 'Approved'
    if _is_awaiting_approval(pull):
        return 'Awaiting approval'
    return 'Not reviewed'


def _pending_table(pending: list[dict]) -> list[str]:
    """Render the longest-pending ready pull-request table."""
    if not pending:
        return ['No ready pull requests are currently open.']

    lines = [
        '| PR | Author | Reactions | Age | Inactive | Review status |',
        '| --- | --- | ---: | ---: | ---: | --- |',
    ]
    for pull in pending[:10]:
        title = _markdown_text(pull.get('title'))
        number = pull.get('number')
        url = pull.get('url')
        author = _markdown_text(pull.get('author') or 'unknown')
        reactions = _format_reactions(pull.get('reactions'))
        lines.append(
            f'| [#{number} — {title}]({url}) | @{author} | [{reactions}]({url}) | {pull["age_days"]} d | '
            f'{pull["inactive_days"]} d | {_review_status(pull)} |'
        )
    lines.append(SORTABLE_TABLE)
    return lines


def render_repository_page(repository: str, cache: dict | None, now: datetime) -> str:
    """Render one repository's Jekyll Markdown report."""
    pulls = cache.get('pull_requests', []) if cache else []
    metrics = calculate(pulls, now)
    collected_text = _format_collected_at(cache.get('collected_at') if cache else None)
    report_cutoff = _search_timestamp(_window_cutoff(now, REPORT_DAYS))
    stale_cutoff = _search_timestamp(_window_cutoff(now, STALE_DAYS))
    current_links = {
        'open': _metric_link(metrics['open'], repository, SEARCH_OPEN),
        'ready': _metric_link(metrics['ready'], repository, SEARCH_OPEN, SEARCH_READY),
        'draft': _metric_link(metrics['draft'], repository, SEARCH_OPEN, SEARCH_DRAFT),
        'not_reviewed': _metric_link(
            metrics['not_reviewed'], repository, SEARCH_OPEN, SEARCH_READY, SEARCH_NO_REVIEW),
        'approval': str(metrics['awaiting_approval']),
        'changes': _metric_link(
            metrics['changes_requested'], repository, SEARCH_OPEN, SEARCH_READY, 'review:changes_requested'),
        'stale': _metric_link(metrics['stale'], repository, SEARCH_OPEN, f'updated:<{stale_cutoff}'),
    }
    completed_links = {
        'opened': _metric_link(metrics['opened'], repository, f'created:>={report_cutoff}'),
        'merged': _metric_link(metrics['merged'], repository, SEARCH_MERGED, f'merged:>={report_cutoff}'),
        'closed': _metric_link(
            metrics['closed_unmerged'], repository, 'is:closed', 'is:unmerged', f'closed:>={report_cutoff}'),
        'no_review': str(metrics['merged_without_review']),
        'no_approval': str(metrics['merged_without_approval']),
    }
    lines = [
        '---',
        f'title: "PR Metrics - {repository}"',
        'layout: page',
        'full-width: true',
        'js:',
        '  - /assets/js/pr-metrics.js',
        f'permalink: /pr-metrics/{repository}/',
        '---',
        '',
        "[← All PR metrics]({{ '/pr-metrics/' | relative_url }})",
        '',
        f'Data collected: **{collected_text}**. Reporting window: **{REPORT_DAYS} days**.',
        '',
    ]
    if not cache:
        lines.extend([
            '> PR metrics have not been collected for this repository yet.',
            '',
        ])

    lines.extend([
        '## Current backlog',
        '',
        '| Open | Ready | Draft | Not reviewed | Awaiting approval | Changes requested | Stale |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        f"| {current_links['open']} | {current_links['ready']} | {current_links['draft']} | "
        f"{current_links['not_reviewed']} | {current_links['approval']} | {current_links['changes']} | "
        f"{current_links['stale']} |",
        SORTABLE_TABLE,
        '',
        f'`Stale` means no activity for at least {STALE_DAYS} days. `Not reviewed` follows GitHub\'s '
        '`review:none` status; comment-only reviews can also be awaiting approval.',
        '',
        f'## Completed work — last {REPORT_DAYS} days',
        '',
        '| Opened | Merged | Closed unmerged | Merge rate | No review | No approval |',
        '| ---: | ---: | ---: | ---: | ---: | ---: |',
        f"| {completed_links['opened']} | {completed_links['merged']} | {completed_links['closed']} | "
        f"{_format_percent(metrics['merge_rate'])} | {completed_links['no_review']} | "
        f"{completed_links['no_approval']} |",
        SORTABLE_TABLE,
        '',
        '| Lead time | Median | 75th percentile |',
        '| --- | ---: | ---: |',
        f"| First review | {_format_duration(metrics['median_first_review_hours'])} | "
        f"{_format_duration(metrics['p75_first_review_hours'])} |",
        f"| First approval | {_format_duration(metrics['median_first_approval_hours'])} | "
        f"{_format_duration(metrics['p75_first_approval_hours'])} |",
        f"| Merge | {_format_duration(metrics['median_merge_hours'])} | "
        f"{_format_duration(metrics['p75_merge_hours'])} |",
        SORTABLE_TABLE,
        '',
        f"Merged changes: **+{metrics['additions']:,} / -{metrics['deletions']:,} lines**.",
        '',
        '## Longest-pending ready PRs',
        '',
    ])

    lines.extend(_pending_table(metrics['pending']))
    lines.append('')
    return '\n'.join(lines)


def render_index_page(caches: dict[str, dict | None], now: datetime) -> str:
    """Render the organization-wide Jekyll Markdown report."""
    all_pulls = [
        pull
        for cache in caches.values()
        for pull in (cache.get('pull_requests', []) if cache else [])
    ]
    totals = calculate(all_pulls, now)
    report_cutoff = _search_timestamp(_window_cutoff(now, REPORT_DAYS))
    stale_cutoff = _search_timestamp(_window_cutoff(now, STALE_DAYS))
    overview_links = {
        'open': _metric_link(totals['open'], None, SEARCH_OPEN),
        'draft': _metric_link(totals['draft'], None, SEARCH_OPEN, SEARCH_DRAFT),
        'not_reviewed': _metric_link(
            totals['not_reviewed'], None, SEARCH_OPEN, SEARCH_READY, SEARCH_NO_REVIEW),
        'approval': str(totals['awaiting_approval']),
        'stale': _metric_link(totals['stale'], None, SEARCH_OPEN, f'updated:<{stale_cutoff}'),
        'merged': _metric_link(totals['merged'], None, SEARCH_MERGED, f'merged:>={report_cutoff}'),
    }
    lines = [
        '---',
        'title: "Pull Request Metrics"',
        'layout: page',
        'full-width: true',
        'js:',
        '  - /assets/js/pr-metrics.js',
        'permalink: /pr-metrics/',
        '---',
        '',
        f'Organization-wide PR health for active repositories. Reporting window: **{REPORT_DAYS} days**.',
        '',
        '## Overview',
        '',
        '| Open | Draft | Not reviewed | Awaiting approval | Stale | Merged | Median merge time |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        f"| {overview_links['open']} | {overview_links['draft']} | {overview_links['not_reviewed']} | "
        f"{overview_links['approval']} | {overview_links['stale']} | {overview_links['merged']} | "
        f"{_format_duration(totals['median_merge_hours'])} |",
        SORTABLE_TABLE,
        '',
        '## Repositories',
        '',
        '| Repository | Open | Draft | Not reviewed | Awaiting approval | Stale | Merged | Median merge |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    repository_rows = []
    for repository, cache in caches.items():
        metrics = calculate(cache.get('pull_requests', []) if cache else [], now)
        repository_rows.append((repository, metrics, cache is not None))
    repository_rows.sort(key=lambda row: (-row[1]['open'], row[0].lower()))

    for repository, metrics, available in repository_rows:
        availability = '' if available else ' ⚠️'
        repo_links = {
            'open': _metric_link(metrics['open'], repository, SEARCH_OPEN),
            'draft': _metric_link(metrics['draft'], repository, SEARCH_OPEN, SEARCH_DRAFT),
            'not_reviewed': _metric_link(
                metrics['not_reviewed'], repository, SEARCH_OPEN, SEARCH_READY, SEARCH_NO_REVIEW),
            'approval': str(metrics['awaiting_approval']),
            'stale': _metric_link(metrics['stale'], repository, SEARCH_OPEN, f'updated:<{stale_cutoff}'),
            'merged': _metric_link(
                metrics['merged'], repository, SEARCH_MERGED, f'merged:>={report_cutoff}'),
        }
        lines.append(
            f"| [{repository}]({{{{ '/pr-metrics/{repository}/' | relative_url }}}}){availability} | "
            f"{repo_links['open']} | {repo_links['draft']} | {repo_links['not_reviewed']} | "
            f"{repo_links['approval']} | {repo_links['stale']} | {repo_links['merged']} | "
            f"{_format_duration(metrics['median_merge_hours'])} |"
        )
    lines.extend([
        SORTABLE_TABLE,
        '',
        f'Generated **{now.strftime("%Y-%m-%d %H:%M UTC")}**. ⚠️ indicates unavailable repository data.',
        '',
    ])
    return '\n'.join(lines)


def write_report_pages(template_dir: str, caches: dict[str, dict | None], now: datetime) -> None:
    """Write the organization index and all repository report pages."""
    report_dir = Path(template_dir) / 'pr-metrics'
    report_dir.mkdir(parents=True, exist_ok=True)
    expected = {'index.md'}
    for repository, cache in caches.items():
        filename = f'{os.path.basename(repository)}.md'
        expected.add(filename)
        (report_dir / filename).write_text(
            render_repository_page(repository, cache, now),
            encoding='utf-8',
        )
    (report_dir / 'index.md').write_text(render_index_page(caches, now), encoding='utf-8')

    for existing in report_dir.glob('*.md'):
        if existing.name not in expected:
            existing.unlink()
