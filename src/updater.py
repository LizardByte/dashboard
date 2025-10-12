# standard imports
import os
from threading import Thread

# lib imports
from crowdin_api import CrowdinClient
from github import Github
import requests
import svgwrite
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
    headers = dict(
        Accept='application/json',
        Authorization=f'bearer {os.environ["CODECOV_TOKEN"]}',
    )
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


def sort_crowdin_data(data):
    data.sort(key=lambda x: (
        -x['data']['approvalProgress'],
        -x['data']['translationProgress'],
        x['data']['language']['name']
    ), reverse=False)

    try:
        en_index = [x['data']['language']['id'] for x in data].index('en')
    except ValueError:
        pass
    else:
        data.insert(0, data.pop(en_index))


def generate_crowdin_svg_graph(data, file_path, project_name):
    line_height = 32
    bar_height = 16
    svg_width = 500
    label_width = 200
    progress_width = 160
    insert = 12
    bar_corner_radius = 0

    dwg = svgwrite.Drawing(filename=f'{file_path}_graph.svg', size=(svg_width, len(data) * line_height))

    dwg.embed_stylesheet("""
    @import url(https://fonts.googleapis.com/css?family=Open+Sans);
    .svg-font {
        font-family: "Open Sans";
        font-size: 12px;
        fill: #999;
    }
    """)

    for lang_base in tqdm(
            iterable=data,
            desc=f'Generating Crowdin graph for project: {project_name}',
    ):
        language = lang_base['data']
        g = dwg.add(dwg.g(
            class_="svg-font",
            transform='translate(0,{})'.format(data.index(lang_base) * line_height)
        ))
        g.add(dwg.text(
            f"{language['language']['name']} ({language['language']['id']})",
            insert=(label_width, 18),
            style='text-anchor:end;')
        )

        translation_progress = language['translationProgress'] / 100.0
        approval_progress = language['approvalProgress'] / 100.0

        progress_insert = (label_width + insert, 6)
        if translation_progress < 100:
            g.add(dwg.rect(
                insert=progress_insert,
                size=(progress_width, bar_height),
                rx=bar_corner_radius,
                ry=bar_corner_radius,
                fill='#999',
                style='filter:opacity(30%);')
            )
        if translation_progress > 0 and approval_progress < 100:
            g.add(dwg.rect(
                insert=progress_insert,
                size=(progress_width * translation_progress, bar_height),
                rx=bar_corner_radius,
                ry=bar_corner_radius,
                fill='#5D89C3')
            )
        if approval_progress > 0:
            g.add(dwg.rect(
                insert=progress_insert,
                size=(progress_width * approval_progress, bar_height),
                rx=bar_corner_radius,
                ry=bar_corner_radius,
                fill='#71C277')
            )

        g.add(dwg.text('{}%'.format(language['translationProgress']),
                       insert=(progress_insert[0] + progress_width + insert, bar_height)))

    dwg.save(pretty=True)


def update_crowdin():
    """
    Cache and update data from Crowdin API, and generate completion graph.
    """
    client = CrowdinClient(
        token=os.environ['CROWDIN_TOKEN'],
        retry_delay=2,
        max_retries=10,
    )

    project_data = client.projects.list_projects()['data']

    for project in tqdm(
            iterable=project_data,
            desc='Updating Crowdin data',
    ):
        project_name = project['data']['name']
        project_id = project['data']['id']
        data = client.translation_status.get_project_progress(projectId=project_id)['data']
        file_path = os.path.join(BASE_DIR, 'crowdin', project_name.replace(' ', '_'))
        helpers.write_json_files(file_path=file_path, data=data)

        sort_crowdin_data(data)
        generate_crowdin_svg_graph(data, file_path, project_name)


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

    fb_endpoints = dict()

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


def update_github():
    """
    Cache and update GitHub Repo banners and data.
    """
    # Initialize PyGithub client
    g = Github(os.environ["GITHUB_TOKEN"])

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
    headers = dict(
        Authorization=f'token {os.environ["GITHUB_TOKEN"]}',
    )
    graphql_url = 'https://api.github.com/graphql'

    for repo in tqdm(
            iterable=repos,
            desc='Updating GitHub data',
    ):
        # skip archived repos
        if repo.archived:
            continue

        # languages - use PyGithub
        languages = repo.get_languages()
        file_path = os.path.join(BASE_DIR, 'github', 'languages', repo.name)
        helpers.write_json_files(file_path=file_path, data=languages)

        # commit activity (last year of activity)
        commit_activity = repo.get_stats_commit_activity()
        commits = [week.raw_data for week in commit_activity]  # Convert PyGithub objects to dict format

        file_path = os.path.join(BASE_DIR, 'github', 'commitActivity', repo.name)
        helpers.write_json_files(file_path=file_path, data=commits)

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
    threads = []

    append_thread_if_env_set(
        env_vars=['DASHBOARD_AUR_REPOS'],
        name='aur',
        target=update_aur,
        threads=threads,
        kwargs=dict(aur_repos=os.getenv('DASHBOARD_AUR_REPOS').split(',')),
    )
    append_thread_if_env_set(
        env_vars=['CODECOV_TOKEN', 'GITHUB_REPOSITORY_OWNER'],
        name='codecov',
        target=update_codecov,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['CROWDIN_TOKEN'],
        name='crowdin',
        target=update_crowdin,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['DISCORD_INVITE'],
        name='discord',
        target=update_discord,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['FACEBOOK_TOKEN', 'FACEBOOK_PAGE_ID'],
        name='facebook',
        target=update_fb,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['GITHUB_TOKEN', 'GITHUB_REPOSITORY_OWNER'],
        name='github',
        target=update_github,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['PATREON_CAMPAIGN_ID'],
        name='patreon',
        target=update_patreon,
        threads=threads,
    )
    append_thread_if_env_set(
        env_vars=['READTHEDOCS_TOKEN'],
        name='readthedocs',
        target=update_readthedocs,
        threads=threads,
    )

    # setup threading exception handling
    if os.getenv('THREADING_EXCEPTION_HANDLER'):
        unhandled_exit.activate()

    for thread in tqdm(
            iterable=threads,
            desc='Starting threads',
    ):
        thread.start()

    # wait for all threads to finish
    for thread in tqdm(
            iterable=threads,
            desc='Waiting for threads to finish',
    ):
        thread.join()

    # deactivate threading exception handling
    if os.getenv('THREADING_EXCEPTION_HANDLER'):
        unhandled_exit.deactivate()
