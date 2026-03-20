# standard imports
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from threading import Thread

# lib imports
from github import Auth, Github
import requests
from tqdm import tqdm
import unhandled_exit

# local imports
from src import BASE_DIR
from src import helpers
from src.logger import log


def update_aur(aur_repos: list):
    """
    Cache and update data from aur API.
    """
    aur_base_url = 'https://aur.archlinux.org/rpc?v=5&type=info&arg='

    for repo in tqdm(
            iterable=aur_repos,
            desc='Updating AUR data',
    ):
        url = f'{aur_base_url}{repo}'
        response = helpers.s.get(url=url)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'aur', repo)
        helpers.write_json_files(file_path=file_path, data=data)


def process_coverage_response(coverage_response, repo_name: str) -> tuple[list, bool]:
    """
    Process a coverage API response and determine if there's more data.
    """
    try:
        coverage_json = coverage_response.json()

        if coverage_response.status_code == 200 and 'results' in coverage_json:
            results = coverage_json['results']
            if results:
                has_more = coverage_json.get('next') is not None
                return results, has_more
    except Exception as e:
        log.warning(f'Error fetching coverage trend for {repo_name}: {e}')

    return [], False


def fetch_coverage_trend_for_repo(base_url: str, repo_name: str, headers: dict) -> list:
    """
    Fetch coverage trend data for a single repository.
    """
    coverage_trend_data = []
    page = 1
    has_more = True

    while has_more:
        coverage_url = f'{base_url}/repos/{repo_name}/coverage/'
        params = {
            'interval': '7d',
            'page': page,
            'page_size': 100,
        }

        coverage_response = helpers.s.get(url=coverage_url, headers=headers, params=params)
        results, has_more = process_coverage_response(coverage_response, repo_name)

        if results:
            coverage_trend_data.extend(results)
            page += 1

    return coverage_trend_data


def update_codecov():
    """
    Get code coverage data from Codecov API.
    """
    archived_repos = set()
    repos_path = os.path.join(BASE_DIR, 'github', 'repos.json')
    if os.path.exists(repos_path):
        try:
            with open(repos_path) as f:
                archived_repos = {r['name'] for r in json.load(f) if r.get('archived')}
        except Exception as e:
            log.warning(f'Could not load GitHub repos for archived check: {e}')

    headers = {
        'Accept': 'application/json',
        'Authorization': f'bearer {os.environ["CODECOV_TOKEN"]}',
    }
    base_url = f'https://codecov.io/api/v2/github/{os.environ["GITHUB_REPOSITORY_OWNER"]}'

    url = f'{base_url}/repos?page_size=500'

    response = helpers.s.get(url=url, headers=headers)
    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError:
        log.error(f'Error: update_codecov: {response.text}')
        raise requests.exceptions.HTTPError(f'Error: {response.text}')

    if response.status_code != 200:
        log.error(f'Error: update_codecov: {data["detail"]}')
        raise requests.exceptions.HTTPError(f'Error: {data["detail"]}')

    assert data['next'] is None, 'More than 500 repos found, need to implement pagination.'

    for repo in tqdm(
            iterable=data['results'],
            desc='Updating Codecov data',
    ):
        if repo['name'] in archived_repos:
            continue

        # Get repo details
        url = f'{base_url}/repos/{repo["name"]}'
        response = helpers.s.get(url=url, headers=headers)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'codecov', repo['name'])
        helpers.write_json_files(file_path=file_path, data=data)

        # Get coverage trend data
        coverage_trend_data = fetch_coverage_trend_for_repo(base_url, repo["name"], headers)

        # Save coverage trend data
        if coverage_trend_data:
            coverage_trend_path = os.path.join(BASE_DIR, 'codecov', f'{repo["name"]}_coverage_trend')
            helpers.write_json_files(file_path=coverage_trend_path, data=coverage_trend_data)


def update_discord():
    """
    Cache and update data from Discord API.
    """
    discord_urls = [
        f'https://discordapp.com/api/invites/{os.environ["DISCORD_INVITE"]}?with_counts=true',
    ]

    for discord_url in tqdm(
            iterable=discord_urls,
            desc='Updating Discord data',
    ):
        response = helpers.s.get(url=discord_url)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'discord', 'invite')
        helpers.write_json_files(file_path=file_path, data=data)


def update_fb():
    """
    Get the number of Facebook page likes and group members.
    """
    fb_base_url = 'https://graph.facebook.com/'

    fb_endpoints = {}

    if os.getenv('FACEBOOK_GROUP_ID'):
        fb_endpoints['group'] = (f'{os.environ["FACEBOOK_GROUP_ID"]}?'
                                 f'fields=member_count,name,description&access_token={os.environ["FACEBOOK_TOKEN"]}')
    if os.getenv('FACEBOOK_PAGE_ID'):
        fb_endpoints['page'] = (f'{os.environ["FACEBOOK_PAGE_ID"]}/'
                                f'insights?metric=page_fans&access_token={os.environ["FACEBOOK_TOKEN"]}')

    for key, value in tqdm(
            iterable=fb_endpoints.items(),
            desc='Updating Facebook data',
    ):
        url = f'{fb_base_url}/{value}'
        response = helpers.s.get(url=url)

        data = response.json()
        try:
            data['paging']
        except KeyError:
            pass
        else:
            # remove facebook token from data
            del data['paging']

        file_path = os.path.join(BASE_DIR, 'facebook', key)
        helpers.write_json_files(file_path=file_path, data=data)


def _get_stats_with_timeout(repo, timeout=60):
    """
    Fetch commit activity for a repo, capping total wait time.

    Parameters
    ----------
    repo :
        PyGithub Repository object.
    timeout : int
        Maximum seconds to wait before giving up (GitHub may return 202 while
        computing stats, causing PyGithub to retry indefinitely without this guard).

    Returns
    -------
    list or None
        Weekly commit-activity objects, or None on timeout.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(repo.get_stats_commit_activity)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            log.warning(f'Timeout fetching commit activity for {repo.name}, skipping.')
            return None


def _seed_star_history(repo, total: int, initial_samples: int) -> list[dict]:
    """
    Fetch evenly-spaced pages from the stargazers API for a first-time seed.

    Parameters
    ----------
    repo :
        PyGithub Repository object.
    total : int
        Current star count (used to calculate page spread).
    initial_samples : int
        Maximum number of pages to request.

    Returns
    -------
    list
        Unsorted list of ``{date, stars}`` dicts sampled across the history.
    """
    per_page = 100
    total_pages = math.ceil(total / per_page)

    if total_pages <= initial_samples:
        pages_to_fetch = list(range(total_pages))
    else:
        pages_to_fetch = sorted({
            round(i * (total_pages - 1) / (initial_samples - 1))
            for i in range(initial_samples)
        })

    history = []
    stargazers = repo.get_stargazers_with_dates()
    for page_idx in pages_to_fetch:
        try:
            page = stargazers.get_page(page_idx)
            if not page:
                continue
            history.append({
                'date': page[0].starred_at.strftime('%Y-%m-%d'),
                'stars': page_idx * per_page + 1,
            })
            if len(page) > 1:
                history.append({
                    'date': page[-1].starred_at.strftime('%Y-%m-%d'),
                    'stars': page_idx * per_page + len(page),
                })
        except Exception as e:
            log.warning(f'Error fetching star history page {page_idx} for {repo.name}: {e}')

    return history


def _collect_star_history(repo, initial_samples: int = 5) -> list:
    """
    Build a cumulative star-history time series for a repository.

    On the first call for a repo the function seeds the history by fetching a
    small number of evenly-spaced API pages (``initial_samples``).  On every
    subsequent call it reads the cached file and appends only today's current
    star count, so **no additional API requests are made after the initial
    seed**.

    Parameters
    ----------
    repo :
        PyGithub Repository object.
    initial_samples : int
        Number of pages to fetch when no cached history exists yet.

    Returns
    -------
    list
        List of dicts with keys ``date`` (YYYY-MM-DD) and ``stars``
        (cumulative star count at that point in time).
    """
    total = repo.stargazers_count
    if total == 0:
        return []

    today = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
    cache_path = os.path.join(BASE_DIR, 'github', 'starHistory', f'{repo.name}.json')

    existing = []
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                existing = json.load(f)
        except Exception:
            pass

    if existing:
        if existing[-1]['date'] == today:
            existing[-1]['stars'] = total
        else:
            existing.append({'date': today, 'stars': total})
        return existing

    history: list[dict] = list(_seed_star_history(repo, total, initial_samples))
    if not history or history[-1]['date'] != today:
        history.append({'date': today, 'stars': total})
    return history


def _process_github_repo(repo, headers: dict, graphql_url: str) -> None:
    """
    Collect and cache all per-repository data for a single GitHub repo.

    Parameters
    ----------
    repo :
        PyGithub Repository object.
    headers : dict
        HTTP headers including the GitHub authorisation token.
    graphql_url : str
        GitHub GraphQL endpoint URL.
    """
    # languages
    languages = repo.get_languages()
    file_path = os.path.join(BASE_DIR, 'github', 'languages', repo.name)
    helpers.write_json_files(file_path=file_path, data=languages)

    # commit activity (last year, weekly buckets)
    commit_activity = _get_stats_with_timeout(repo)
    if commit_activity:
        commits = [week.raw_data for week in commit_activity]
        file_path = os.path.join(BASE_DIR, 'github', 'commitActivity', repo.name)
        helpers.write_json_files(file_path=file_path, data=commits)

    # open pull requests
    pulls_data = []
    for pr in repo.get_pulls(state='open'):
        pulls_data.append({
            'number': pr.number,
            'title': pr.title,
            'author': pr.user.login,
            'labels': [label.name for label in pr.labels],
            'assignees': [assignee.login for assignee in pr.assignees],
            'created_at': pr.created_at.isoformat(),
            'updated_at': pr.updated_at.isoformat(),
            'draft': pr.draft,
            'milestone': pr.milestone.title if pr.milestone else None,
        })
    file_path = os.path.join(BASE_DIR, 'github', 'pulls', repo.name)
    helpers.write_json_files(file_path=file_path, data=pulls_data)

    # star history (sampled to cap API calls)
    star_history = _collect_star_history(repo)
    if star_history:
        file_path = os.path.join(BASE_DIR, 'github', 'starHistory', repo.name)
        helpers.write_json_files(file_path=file_path, data=star_history)

    # openGraphImages - uses GraphQL
    query = """
    {
      repository(owner: "%s", name: "%s") {
        openGraphImageUrl
      }
    }
    """ % (repo.owner.login, repo.name)

    response = helpers.s.post(url=graphql_url, json={'query': query}, headers=headers)
    repo_data = response.json()
    try:
        image_url = repo_data['data']['repository']['openGraphImageUrl']
    except KeyError:
        log.error(f'Error: update_github: {repo_data}')
        raise SystemExit('"GITHUB_TOKEN" is invalid.')
    if 'avatars' not in image_url:
        file_path = os.path.join(BASE_DIR, 'github', 'openGraphImages', repo.name)
        helpers.save_image_from_url(
            file_path=file_path,
            file_extension='png',
            image_url=image_url,
            size_x=624,
            size_y=312,
        )


def update_github():
    """
    Cache and update GitHub Repo banners and data.
    """
    g = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]), timeout=30)
    g.per_page = 100

    # Get the user/organization
    owner = g.get_user(os.environ["GITHUB_REPOSITORY_OWNER"])

    # Get all repositories
    repos = list(owner.get_repos())

    # Convert PyGithub repo objects to dict format for JSON serialization
    repos_data = []
    for repo in repos:
        repos_data.append(repo.raw_data)

    file_path = os.path.join(BASE_DIR, 'github', 'repos')
    helpers.write_json_files(file_path=file_path, data=repos_data)

    # GraphQL query still uses direct requests
    headers = {
        'Authorization': f'token {os.environ["GITHUB_TOKEN"]}',
    }
    graphql_url = 'https://api.github.com/graphql'

    for repo in tqdm(
            iterable=repos,
            desc='Updating GitHub data',
    ):
        if repo.archived:
            continue
        _process_github_repo(repo, headers, graphql_url)


def update_patreon():
    """
    Get patron count from Patreon.

    Patreon id can be obtained in browser developer console using the following javascript:
    `window.patreon.bootstrap.campaign.data.id`
    """
    patreon_urls = [
        F'https://www.patreon.com/api/campaigns/{os.environ["PATREON_CAMPAIGN_ID"]}',
    ]

    for patreon_url in tqdm(
            iterable=patreon_urls,
            desc='Updating Patreon data',
    ):
        response = helpers.cs.get(url=patreon_url)

        data = response.json()['data']['attributes']

        file_path = os.path.join(BASE_DIR, 'patreon', 'LizardByte')
        helpers.write_json_files(file_path=file_path, data=data)


def readthedocs_loop(url: str, file_path: str) -> list:
    headers = {
        'Authorization': f'token {os.environ["READTHEDOCS_TOKEN"]}',
        'Accept': 'application/json'
    }

    results = []

    while True:
        response = helpers.rtd_s.get(url=url, headers=headers)
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            break

        try:
            results.extend(data['results'])
        except KeyError:
            pass

        try:
            url = data['next']
        except KeyError:
            url = None

        if not url:
            break

    if results:
        helpers.write_json_files(file_path=file_path, data=results)

    return results


def update_readthedocs():
    """
    Cache and update readthedocs info.
    """
    url_base = 'https://readthedocs.org'
    url = f'{url_base}/api/v3/projects/'

    file_path = os.path.join(BASE_DIR, 'readthedocs', 'projects')
    projects = readthedocs_loop(url=url, file_path=file_path)

    for project in tqdm(
            iterable=projects,
            desc='Updating Readthedocs data',
    ):
        git_url = project['repository']['url']
        repo_name = git_url.rsplit('/', 1)[-1].rsplit('.git', 1)[0]

        skip_links = [
            'builds',  # skip builds, too much data and too slow
            'environmentvariables',  # not needed
            'notifications',  # not needed
        ]
        for link in project['_links']:
            if link in skip_links:
                continue

            file_path = os.path.join(BASE_DIR, 'readthedocs', link, repo_name)

            url = project['_links'][link]
            readthedocs_loop(url=url, file_path=file_path)


def append_thread_if_env_set(
        env_vars: list,
        name: str,
        target: callable,
        threads: list,
        kwargs: dict = None,
):
    if all(os.getenv(var) for var in env_vars):
        threads.append(Thread(name=name, target=target, kwargs=kwargs))


def update():
    # Threads that are fully independent of each other and of GitHub data.
    independent_threads = []

    append_thread_if_env_set(
        env_vars=['DASHBOARD_AUR_REPOS'],
        name='aur',
        target=update_aur,
        threads=independent_threads,
        kwargs={'aur_repos': os.getenv('DASHBOARD_AUR_REPOS').split(',')},
    )
    append_thread_if_env_set(
        env_vars=['DISCORD_INVITE'],
        name='discord',
        target=update_discord,
        threads=independent_threads,
    )
    append_thread_if_env_set(
        env_vars=['FACEBOOK_TOKEN', 'FACEBOOK_PAGE_ID'],
        name='facebook',
        target=update_fb,
        threads=independent_threads,
    )
    append_thread_if_env_set(
        env_vars=['PATREON_CAMPAIGN_ID'],
        name='patreon',
        target=update_patreon,
        threads=independent_threads,
    )
    append_thread_if_env_set(
        env_vars=['READTHEDOCS_TOKEN'],
        name='readthedocs',
        target=update_readthedocs,
        threads=independent_threads,
    )

    # GitHub must finish before Codecov so that repos.json is up to date and
    # update_codecov() can correctly skip archived repos.
    github_threads = []
    append_thread_if_env_set(
        env_vars=['GITHUB_TOKEN', 'GITHUB_REPOSITORY_OWNER'],
        name='github',
        target=update_github,
        threads=github_threads,
    )

    codecov_threads = []
    append_thread_if_env_set(
        env_vars=['CODECOV_TOKEN', 'GITHUB_REPOSITORY_OWNER'],
        name='codecov',
        target=update_codecov,
        threads=codecov_threads,
    )

    # setup threading exception handling
    if os.getenv('THREADING_EXCEPTION_HANDLER'):
        unhandled_exit.activate()

    # Phase 1: start independent threads and GitHub in parallel.
    for thread in tqdm(iterable=independent_threads + github_threads, desc='Starting threads'):
        thread.start()

    # Wait for GitHub before starting Codecov.
    for thread in tqdm(iterable=github_threads, desc='Waiting for GitHub thread'):
        thread.join()

    # Phase 2: start Codecov now that repos.json is fresh.
    for thread in tqdm(iterable=codecov_threads, desc='Starting Codecov thread'):
        thread.start()

    # Wait for all remaining threads.
    for thread in tqdm(iterable=independent_threads + codecov_threads, desc='Waiting for threads to finish'):
        thread.join()

    # deactivate threading exception handling
    if os.getenv('THREADING_EXCEPTION_HANDLER'):
        unhandled_exit.deactivate()


if __name__ == '__main__':
    update()
