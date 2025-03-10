# standard imports
import os
from threading import Thread
import time

# lib imports
from crowdin_api import CrowdinClient
import requests
import svgwrite
from tqdm import tqdm
import unhandled_exit

# local imports
from src import BASE_DIR
from src import helpers


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


def update_codecov():
    """
    Get code coverage data from Codecov API.
    """
    headers = dict(
        Accept='application/json',
        Authorization=f'bearer {os.environ["CODECOV_TOKEN"]}',
    )
    base_url = f'https://codecov.io/api/v2/gh/{os.environ["GITHUB_REPOSITORY_OWNER"]}'

    url = f'{base_url}/repos?page_size=500'

    max_tries = 5
    count = 0
    data = None
    response = None
    while count < max_tries:
        response = helpers.s.get(url=url, headers=headers)
        try:
            data = response.json()
        except requests.exceptions.JSONDecodeError:
            count += 1
            time.sleep(2 ** count)  # exponential backoff
            continue
        break

    if not data:
        raise requests.exceptions.HTTPError(f'Error: {response.text}')

    if response.status_code != 200:
        raise requests.exceptions.HTTPError(f'Error: {data["detail"]}')

    assert data['next'] is None, 'More than 500 repos found, need to implement pagination.'

    for repo in tqdm(
            iterable=data['results'],
            desc='Updating Codecov data',
    ):
        url = f'{base_url}/repos/{repo["name"]}'
        response = helpers.s.get(url=url, headers=headers)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'codecov', repo['name'])
        helpers.write_json_files(file_path=file_path, data=data)


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
        response = requests.get(url=url)

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
    url = f'https://api.github.com/users/{os.environ["GITHUB_REPOSITORY_OWNER"]}/repos'
    per_page = 100
    repos = []

    headers = dict(
        accept='application/vnd.github.v3+json',
    )

    query_params = dict(
        per_page=per_page,
        page=1,
    )

    while True:
        response = helpers.s.get(
            url=url,
            headers=headers,
            params=query_params,
        )
        response_data = response.json()
        repos.extend(response_data)

        if len(response_data) < per_page:
            break

        query_params['page'] += 1

    file_path = os.path.join(BASE_DIR, 'github', 'repos')
    helpers.write_json_files(file_path=file_path, data=repos)

    headers = dict(
        Authorization=f'token {os.environ["GITHUB_TOKEN"]}',
    )
    url = 'https://api.github.com/graphql'

    for repo in tqdm(
            iterable=repos,
            desc='Updating GitHub data',
    ):
        # languages
        response = helpers.s.get(url=repo['languages_url'], headers=headers)
        # if TypeError, API limit has likely been exceeded or possible issue with GitHub API...
        # https://www.githubstatus.com/
        # do not error handle, better that workflow fails

        languages = response.json()

        file_path = os.path.join(BASE_DIR, 'github', 'languages', repo['name'])
        helpers.write_json_files(file_path=file_path, data=languages)

        # openGraphImages
        query = """
        {
          repository(owner: "%s", name: "%s") {
            openGraphImageUrl
          }
        }
        """ % (repo['owner']['login'], repo['name'])

        response = helpers.s.post(url=url, json={'query': query}, headers=headers)
        repo_data = response.json()
        try:
            image_url = repo_data['data']['repository']['openGraphImageUrl']
        except KeyError:
            raise SystemExit('"GITHUB_TOKEN" is invalid.')
        if 'avatars' not in image_url:
            file_path = os.path.join(BASE_DIR, 'github', 'openGraphImages', repo['name'])
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
        response = helpers.s.get(url=patreon_url)

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
        response = helpers.s.get(url=url, headers=headers)
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

        for link in project['_links']:
            if link == 'builds':
                continue  # skip builds, too much data and too slow

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
