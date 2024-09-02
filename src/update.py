# standard imports
import argparse
import json
import os
import pathlib
from threading import Thread
from typing import Union

# lib imports
import cloudscraper
from crowdin_api import CrowdinClient
from dotenv import load_dotenv
from PIL import Image
import requests
from requests.adapters import HTTPAdapter
import svgwrite
from tqdm import tqdm
import unhandled_exit

# setup environment if running locally
load_dotenv()

# setup threading exception handling
unhandled_exit.activate()

# setup requests session
s = cloudscraper.CloudScraper()  # CloudScraper inherits from requests.Session
retry_adapter = HTTPAdapter(max_retries=5)
s.mount('https://', retry_adapter)

# constants
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'gh-pages')


# custom print
def debug_print(
        *values: object,
        sep: Union[str, None] = ' ',
        end: Union[str, None] = '\n',
):
    if os.getenv('ACTIONS_RUNNER_DEBUG') or os.getenv('ACTIONS_STEP_DEBUG'):
        print(*values, sep=sep, end=end)


def save_image_from_url(file_path: str, file_extension: str, image_url: str, size_x: int = 0, size_y: int = 0):
    """
    Write image data to file. If ``size_x`` and ``size_y`` are both supplied, a resized image will also be saved.

    Parameters
    ----------
    file_path : str
        The file path to save the file at.
    file_extension : str
        The extension of the file name.
    image_url : str
        The image url.
    size_x : int
        The ``x`` dimension to resize the image to. If used, ``size_y`` must also be defined.
    size_y : int
        The ``y`` dimension to resize the image to. If used, ``size_x`` must also be defined.
    """
    debug_print(f'Saving image from {image_url}')
    # determine the directory
    directory = os.path.dirname(file_path)

    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    og_img_data = s.get(url=image_url).content

    file_name_with_ext = f'{file_path}.{file_extension}'
    with open(file_name_with_ext, 'wb') as handler:
        handler.write(og_img_data)

    # resize the image
    if size_x and size_y:
        pil_img_data = Image.open(file_name_with_ext)
        resized_img_data = pil_img_data.resize((size_x, size_y))
        resized_img_data.save(fp=f'{file_path}_{size_x}x{size_y}.{file_extension}')


def write_json_files(file_path: str, data: any):
    """
    Write dictionary to json file.

    Parameters
    ----------
    file_path : str
        The file path to save the file at, excluding the file extension which will be `.json`
    data
        The dictionary data to write in the json file.
    """
    debug_print(f'Writing json file at {file_path}')
    # determine the directory
    directory = os.path.dirname(file_path)

    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

    with open(f'{file_path}.json', 'w') as f:
        json.dump(obj=data, fp=f, indent=args.indent)


def update_aur():
    """
    Cache and update data from aur API.
    """
    aur_base_url = 'https://aur.archlinux.org/rpc?v=5&type=info&arg='
    aur_repos = ['sunshine']

    for repo in tqdm(
            iterable=aur_repos,
            desc='Updating AUR data',
    ):
        url = f'{aur_base_url}{repo}'
        response = s.get(url=url)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'aur', repo)
        write_json_files(file_path=file_path, data=data)


def update_codecov():
    """
    Get code coverage data from Codecov API.
    """
    headers = dict(
        Accept='application/json',
        Authorization=f'bearer {args.codecov_token}',
    )
    base_url = f'https://codecov.io/api/v2/gh/{args.github_repository_owner}'

    url = f'{base_url}/repos?page_size=500'

    response = s.get(url=url, headers=headers)
    data = response.json()
    assert data['next'] is None, 'More than 500 repos found, need to implement pagination.'

    for repo in tqdm(
            iterable=data['results'],
            desc='Updating Codecov data',
    ):
        url = f'{base_url}/repos/{repo["name"]}'
        response = s.get(url=url, headers=headers)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'codecov', repo['name'])
        write_json_files(file_path=file_path, data=data)


def update_crowdin():
    """
    Cache and update data from Crowdin API, and generate completion graph.
    """
    client = CrowdinClient(token=args.crowdin_token)

    # automatically collect crowdin projects
    project_data = client.projects.list_projects()['data']

    for project in tqdm(
            iterable=project_data,
            desc='Updating Crowdin data',
    ):
        project_name = project['data']['name']
        project_id = project['data']['id']
        data = client.translation_status.get_project_progress(projectId=project_id)['data']
        file_path = os.path.join(BASE_DIR, 'crowdin', project_name.replace(' ', '_'))
        write_json_files(file_path=file_path, data=data)

        # sort data by approval progress first, then translation progress, then alphabetically
        data.sort(key=lambda x: (
            -x['data']['approvalProgress'],
            -x['data']['translationProgress'],
            x['data']['language']['name']
        ), reverse=False)

        # ensure "en" is first, if it exists
        try:
            en_index = [x['data']['language']['id'] for x in data].index('en')
        except ValueError:
            pass
        else:
            data.insert(0, data.pop(en_index))

        # generate translation and approval completion graph
        line_height = 32
        bar_height = 16
        svg_width = 500
        label_width = 200
        progress_width = 160
        insert = 12
        bar_corner_radius = 0

        dwg = svgwrite.Drawing(filename=f'{file_path}_graph.svg', size=(svg_width, len(data) * line_height))

        # load css font
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

            # write the svg file
            dwg.save(pretty=True)


def update_discord():
    """
    Cache and update data from Discord API.
    """
    discord_urls = [
        f'https://discordapp.com/api/invites/{args.discord_invite}?with_counts=true',
    ]

    for discord_url in tqdm(
            iterable=discord_urls,
            desc='Updating Discord data',
    ):
        response = s.get(url=discord_url)
        data = response.json()

        file_path = os.path.join(BASE_DIR, 'discord', 'invite')
        write_json_files(file_path=file_path, data=data)


def update_fb():
    """
    Get number of Facebook page likes and group members.
    """
    fb_base_url = 'https://graph.facebook.com/'

    fb_endpoints = dict(
        group=f'{args.facebook_group_id}?fields=member_count,name,description&access_token={args.facebook_token}',
        page=f'{args.facebook_page_id}/insights?metric=page_fans&access_token={args.facebook_token}'
    )

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
        write_json_files(file_path=file_path, data=data)


def update_github():
    """
    Cache and update GitHub Repo banners.
    """
    url = f'https://api.github.com/users/{args.github_repository_owner}/repos'
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
        response = s.get(
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
    write_json_files(file_path=file_path, data=repos)

    headers = dict(
        Authorization=f'token {args.github_token}'
    )
    url = 'https://api.github.com/graphql'

    for repo in tqdm(
            iterable=repos,
            desc='Updating GitHub data',
    ):
        # languages
        response = s.get(url=repo['languages_url'], headers=headers)
        # if TypeError, API limit has likely been exceeded or possible issue with GitHub API...
        # https://www.githubstatus.com/
        # do not error handle, better that workflow fails

        languages = response.json()

        file_path = os.path.join(BASE_DIR, 'github', 'languages', repo['name'])
        write_json_files(file_path=file_path, data=languages)

        # openGraphImages
        query = """
        {
          repository(owner: "%s", name: "%s") {
            openGraphImageUrl
          }
        }
        """ % (repo['owner']['login'], repo['name'])

        response = s.post(url=url, json={'query': query}, headers=headers)
        repo_data = response.json()
        try:
            image_url = repo_data['data']['repository']['openGraphImageUrl']
        except KeyError:
            raise SystemExit('"GITHUB_TOKEN" is invalid.')
        if 'avatars' not in image_url:
            file_path = os.path.join(BASE_DIR, 'github', 'openGraphImages', repo['name'])
            save_image_from_url(file_path=file_path, file_extension='png', image_url=image_url, size_x=624, size_y=312)


def update_patreon():
    """
    Get patron count from Patreon.

    Patreon id can be obtained in browser developer console using the folliwng javascript:
    `window.patreon.bootstrap.campaign.data.id`
    """
    patreon_urls = [
        'https://www.patreon.com/api/campaigns/6131567',
    ]

    for patreon_url in tqdm(
            iterable=patreon_urls,
            desc='Updating Patreon data',
    ):
        response = s.get(url=patreon_url)

        data = response.json()['data']['attributes']

        file_path = os.path.join(BASE_DIR, 'patreon', 'LizardByte')
        write_json_files(file_path=file_path, data=data)


def readthedocs_loop(url: str, file_path: str) -> list:
    headers = {
        'Authorization': f'token {args.readthedocs_token}',
        'Accept': 'application/json'
    }

    results = []

    while True:
        response = s.get(url=url, headers=headers)
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
        write_json_files(file_path=file_path, data=results)

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


def missing_arg():
    parser.print_help()
    raise SystemExit(1)


if __name__ == '__main__':
    # setup arguments using argparse
    parser = argparse.ArgumentParser(description="Update github pages.")
    parser.add_argument('--codecov_token', type=str, required=False, default=os.getenv('CODECOV_TOKEN'),
                        help='Codecov API token.')
    parser.add_argument('--crowdin_token', type=str, required=False, default=os.getenv('CROWDIN_TOKEN'),
                        help='Crowdin API token.')
    parser.add_argument('--discord_invite', type=str, required=False, default=os.getenv('DISCORD_INVITE'),
                        help='Discord invite code.')
    parser.add_argument('--facebook_group_id', type=str, required=False, default=os.getenv('FACEBOOK_GROUP_ID'),
                        help='Facebook group ID.')
    parser.add_argument('--facebook_page_id', type=str, required=False, default=os.getenv('FACEBOOK_PAGE_ID'),
                        help='Facebook page ID.')
    parser.add_argument('--facebook_token', type=str, required=False, default=os.getenv('FACEBOOK_TOKEN'),
                        help='Facebook Token, requires `groups_access_member_info`, `read_insights`, and '
                             '`pages_read_engagement`. Must be a `page` token, not a `user` token. Token owner must be '
                             'admin of the group.')
    parser.add_argument('--github_repository_owner', type=str, required=False,
                        default=os.getenv('GITHUB_REPOSITORY_OWNER'),
                        help='GitHub Username. Can use environment variable "GITHUB_REPOSITORY_OWNER"')
    parser.add_argument('--github_token', type=str, required=False, default=os.getenv('GITHUB_TOKEN'),
                        help='GitHub Token, no scope selection is necessary. Can use environment variable '
                             '"GITHUB_TOKEN"')
    parser.add_argument('--readthedocs_token', type=str, required=False, default=os.getenv('READTHEDOCS_TOKEN'),
                        help='Readthedocs API token. Can use environment variable "READTHEDOCS_TOKEN"')
    parser.add_argument('-i', '--indent_json', action='store_true', help='Indent json files.')

    args = parser.parse_args()
    args.indent = 4 if args.indent_json else None

    if not args.codecov_token or not args.discord_invite or not args.facebook_group_id or not args.facebook_page_id or \
            not args.facebook_token or not args.github_repository_owner or not args.github_token or \
            not args.readthedocs_token:
        missing_arg()

    threads = [
        Thread(
            name='aur',
            target=update_aur,
        ),
        Thread(
            name='codecov',
            target=update_codecov,
        ),
        Thread(
            name='crowdin',
            target=update_crowdin,
        ),
        Thread(
            name='discord',
            target=update_discord,
        ),
        Thread(
            name='facebook',
            target=update_fb,
        ),
        Thread(
            name='github',
            target=update_github,
        ),
        Thread(
            name='patreon',
            target=update_patreon,
        ),
        Thread(
            name='readthedocs',
            target=update_readthedocs,
        ),
    ]

    for thread in tqdm(
            iterable=threads,
            desc='Starting threads',
    ):
        thread.start()
