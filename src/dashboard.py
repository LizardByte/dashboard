# standard imports
import json
import os

# lib imports
from github import Github, PaginatedList, UnknownObjectException
from IPython.display import HTML, display
from itables import init_notebook_mode, show
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

# local imports
from src import BASE_DIR
from src import updater

# Authenticate with GitHub
token = os.getenv("GITHUB_TOKEN")
g = Github(token)

# set the default plotly template
pio.templates.default = "plotly_dark"

# Fetch repository data
org_name = "LizardByte"
org = g.get_organization(org_name)

# constants
text_template = '%{text}'


# globals
updated = False
repos = []
df_all_repos = pd.DataFrame()
df_repos = pd.DataFrame()
df_pr_details = pd.DataFrame()
df_language_data = pd.DataFrame()
df_docs_data = pd.DataFrame()


def init():
    global updated
    if not updated:
        update_html_head_title()
        updater.update()
        updated = True


def update_html_head_title():
    script = """
    <script>
      document.addEventListener('DOMContentLoaded', function() {
        document.title = 'Dashboard | LizardByte';
      });
    </script>
    """
    display(HTML(script))


def get_repos() -> PaginatedList:
    global repos
    repos = org.get_repos()
    return repos


def get_repo_data() -> pd.DataFrame:
    global df_all_repos

    if not repos:
        get_repos()

    # all readthedocs projects
    readthedocs_path = os.path.join(BASE_DIR, 'readthedocs', 'projects.json')
    with open(readthedocs_path, 'r') as f:
        readthedocs_data = json.load(f)

    repo_data = []
    for repo in repos:
        # skip private repos
        if repo.private:
            continue

        # get license
        license_name = repo.license.name if repo.license else "No License"

        # split open issues and PRs
        open_issues = repo.get_issues(state='open')
        open_prs = [issue for issue in open_issues if issue.pull_request is not None]
        open_issues = [issue for issue in open_issues if issue.pull_request is None]

        # coverage data
        coverage = 0
        try:
            with open(os.path.join(BASE_DIR, 'codecov', f'{repo.name}.json')) as f:
                coverage_data = json.load(f)
            coverage = coverage_data['totals']['coverage']
        except Exception:
            pass

        # readthedocs data
        readthedocs_project = None
        for project in readthedocs_data:
            if project['repository']['url'] == repo.clone_url:
                readthedocs_project = project

        # has README.md or README.rst
        # check if the repo has a README.md or README.rst
        readme_file = None
        try:
            readme_file = repo.get_readme()
        except UnknownObjectException:
            pass

        repo_data.append({
            "repo": repo.name,
            "stars": repo.stargazers_count,
            "archived": repo.archived,
            "fork": repo.fork,
            "forks": repo.forks_count,
            "issues": open_issues,
            "topics": repo.get_topics(),
            "languages": repo.get_languages(),
            "license": license_name,
            "prs": open_prs,
            "created_at": repo.created_at,
            "updated_at": repo.updated_at,
            "coverage": coverage,
            "readthedocs": readthedocs_project,
            "has_readthedocs": readthedocs_project is not None,
            "has_readme": readme_file is not None,
            "_repo": repo,
        })

    df_all_repos = pd.DataFrame(repo_data)
    return df_all_repos


def get_df_repos() -> pd.DataFrame:
    global df_repos

    if df_all_repos.empty:
        get_repo_data()

    df_repos = df_all_repos[
        (~df_all_repos['archived']) &
        (~df_all_repos['topics'].apply(lambda topics: 'package-manager' in topics))
    ]
    return df_repos


def show_star_gazers():
    if df_repos.empty:
        get_df_repos()

    df_stars = df_repos.sort_values(
        by='stars',
        ascending=False,
    )
    df_stars['log_stars'] = np.log1p(df_stars['stars'])
    fig = px.bar(
        df_stars,
        x='repo',
        y='log_stars',
        title='Stars',
        text='stars',
    )
    fig.update_traces(
        texttemplate=text_template,
        textposition='inside',
    )
    fig.update_layout(
        yaxis_title=None,
        yaxis_showticklabels=False,
    )
    fig.show()


def get_stargazer_data() -> list:
    if df_repos.empty:
        get_df_repos()

    stargazer_data = []
    for repo in df_repos.to_dict('records'):
        stargazers = repo['_repo'].get_stargazers_with_dates()
        for stargazer in stargazers:
            stargazer_data.append({
                "repo": repo['repo'],
                "date": stargazer.starred_at,
            })

    return stargazer_data


def show_star_history():
    df_stargazers = pd.DataFrame(get_stargazer_data())
    df_stargazers = df_stargazers.sort_values(by="date")
    df_stargazers["cumulative_stars"] = df_stargazers.groupby("repo").cumcount() + 1

    fig = px.line(
        df_stargazers,
        x="date",
        y="cumulative_stars",
        color="repo",
        title="Star History",
        labels={"date": "Date", "cumulative_stars": "Cumulative Stars"},
    )
    fig.show()


def show_forks():
    if df_repos.empty:
        get_df_repos()

    df_forks = df_repos.sort_values(
        by='forks',
        ascending=False,
    )
    df_forks['log_forks'] = np.log1p(df_forks['forks'])
    fig = px.bar(
        df_forks,
        x='repo',
        y='log_forks',
        title='Forks',
        text='forks',
    )
    fig.update_traces(
        texttemplate=text_template,
        textposition='inside',
    )
    fig.update_layout(
        yaxis_title=None,
        yaxis_showticklabels=False,
    )
    fig.show()


def show_issues():
    if df_repos.empty:
        get_df_repos()

    df_issues = df_repos.copy()
    df_issues['issue_count'] = df_issues['issues'].apply(len)
    df_issues = df_issues.sort_values(by='issue_count', ascending=False)
    df_issues['log_issues'] = np.log1p(df_issues['issue_count'])
    fig = px.bar(
        df_issues,
        x='repo',
        y='log_issues',
        title='Open Issues',
        text='issue_count',
    )
    fig.update_traces(
        texttemplate=text_template,
        textposition='inside',
    )
    fig.update_layout(
        yaxis_title=None,
        yaxis_showticklabels=False,
    )
    fig.show()


def get_pr_data() -> pd.DataFrame:
    global df_pr_details

    if df_repos.empty:
        get_df_repos()

    pr_data = []
    for repo in df_repos.to_dict('records'):
        for pr in repo['prs']:
            pr_details = repo['_repo'].get_pull(pr.number)

            # Check if the PR has been approved
            reviews = pr_details.get_reviews()
            approved = any(review.state == 'APPROVED' for review in reviews)

            # Get the milestone
            milestone = pr_details.milestone.title if pr_details.milestone else None

            pr_data.append({
                "repo": repo['repo'],
                "number": pr_details.number,
                "title": pr_details.title,
                "author": pr_details.user.login,
                "labels": [label.name for label in pr_details.labels],
                "assignees": [assignee.login for assignee in pr_details.assignees],
                "created_at": pr_details.created_at,
                "last_activity": pr_details.updated_at,
                "status": "Draft" if pr_details.draft else "Ready",
                "approved": approved,
                "milestone": milestone,
            })

    df_pr_details = pd.DataFrame(pr_data)
    return df_pr_details


def show_pr_graph():
    if df_pr_details.empty:
        get_pr_data()

    # Group by repository and status to get the count of PRs
    df_pr_counts = df_pr_details.groupby(['repo', 'status']).size().reset_index(name='pr_count')

    # Sort repositories by total PR count
    df_pr_counts['total_prs'] = df_pr_counts.groupby('repo')['pr_count'].transform('sum')
    df_pr_counts = df_pr_counts.sort_values(by='total_prs', ascending=False)

    # Create Stacked Bar Chart
    fig_bar = px.bar(
        df_pr_counts,
        x='repo',
        y='pr_count',
        color='status',
        title='Open Pull Requests',
        labels={'pr_count': 'Count of PRs', 'repo': 'Repository', 'status': 'PR Status'},
        text='pr_count',
        category_orders={'repo': df_pr_counts['repo'].tolist()},
    )

    fig_bar.update_layout(
        yaxis_title='Open PRs',
        xaxis_title='Repository',
    )
    fig_bar.update_traces(
        texttemplate=text_template,
        textposition='inside',
    )
    fig_bar.show()


def show_pr_table():
    if df_pr_details.empty:
        get_pr_data()

    # darken the column filter inputs
    css = """
    .dt-column-title input[type="text"] {
      background-color: var(--jp-layout-color0);
      border-color: rgb(64,67,70);
      border-width: 1px;
      color: var(--jp-ui-font-color1);
    }
    """
    display(HTML(f"<style>{css}</style>"))

    init_notebook_mode(
        all_interactive=True,
        connected=False,
    )

    # Display the DataFrame as an interactive table using itables
    table_download_name = "LizardByte-Pull-Requests"
    show(
        df_pr_details,
        buttons=[
            "pageLength",
            "copyHtml5",
            {"extend": "csvHtml5", "title": table_download_name},
            {"extend": "excelHtml5", "title": table_download_name},
        ],
        classes="display compact",
        column_filters="header",
        layout={"topEnd": None},
    )


def show_license_distribution():
    if df_repos.empty:
        get_df_repos()

    license_counts = df_repos.groupby(['license', 'repo']).size().reset_index(name='count')

    fig_treemap = px.treemap(
        license_counts,
        path=['license', 'repo'],
        values='count',
        title='License Distribution',
        hover_data={'repo': True, 'count': False},
    )
    fig_treemap.show()


def show_coverage():
    if df_repos.empty:
        get_df_repos()

    df_coverage = df_repos.sort_values(
        by='coverage',
        ascending=False,
    )

    # inverse marker size, so higher coverage has smaller markers
    df_coverage['marker_size'] = df_coverage['coverage'].apply(lambda x: 110 - x if x > 0 else 0)

    fig_scatter = px.scatter(
        df_coverage,
        x='repo',
        y='coverage',
        title='Coverage Percentage',
        size='marker_size',
        color='coverage',
        color_continuous_scale=['red', 'yellow', 'green'],  # red is low, green is high
    )
    fig_scatter.update_layout(
        yaxis_title='Coverage Percentage',
        xaxis_title='Repository',
    )
    fig_scatter.show()


def get_language_data():
    global df_language_data

    language_data = []
    for repo in df_repos.to_dict('records'):
        for language, bytes_of_code in repo['languages'].items():
            language_data.append({
                "repo": repo['repo'],
                "language": language,
                "bytes_of_code": bytes_of_code,
            })

    df_language_data = pd.DataFrame(language_data)
    return df_language_data


def show_language_data():
    if df_language_data.empty:
        get_language_data()

    # Aggregate data by language and repo
    language_counts_bytes = df_language_data.groupby(['language', 'repo']).agg({
        'bytes_of_code': 'sum'
    }).reset_index()
    language_counts_repos = df_language_data.groupby(['language', 'repo']).size().reset_index(name='repo_count')

    def create_language_figures(counts: pd.DataFrame, path_key: str, value_key: str):
        _fig_treemap = px.treemap(
            counts,
            path=[path_key, 'repo'],
            values=value_key,
        )
        _fig_sunburst = px.sunburst(
            counts,
            path=[path_key, 'repo'],
            values=value_key,
        )
        return _fig_treemap, _fig_sunburst

    # List of tuples containing the data and titles for each figure
    figures_data = [
        (language_counts_bytes, 'language', 'bytes_of_code', 'Programming Languages by Bytes of Code'),
        (language_counts_repos, 'language', 'repo_count', 'Programming Languages by Repo Count')
    ]

    # Loop through the list to create figures and add traces
    for _counts, _path_key, value_key, title in figures_data:
        fig_treemap, fig_sunburst = create_language_figures(counts=_counts, path_key=_path_key, value_key=value_key)

        fig = go.Figure()
        fig.add_trace(fig_treemap.data[0])
        fig.add_trace(fig_sunburst.data[0])
        fig.data[1].visible = False

        fig.update_layout(
            title=title,
            updatemenus=[
                {
                    "buttons": [
                        {
                            "label": "Treemap",
                            "method": "update",
                            "args": [
                                {"visible": [True, False]},
                            ],
                        },
                        {
                            "label": "Sunburst",
                            "method": "update",
                            "args": [
                                {"visible": [False, True]},
                            ],
                        },
                    ],
                    "direction": "down",
                    "showactive": True,
                }
            ]
        )
        fig.show()


def get_docs_data():
    global df_docs_data

    docs_data = []
    for repo in df_repos.to_dict('records'):
        docs_data.append({
            "repo": repo['repo'],
            "has_readme": repo['has_readme'],
            "has_readthedocs": repo['has_readthedocs'],
        })

    df_docs_data = pd.DataFrame(docs_data)
    return df_docs_data


def show_docs_data():
    if df_docs_data.empty:
        get_docs_data()

    readme_counts = df_docs_data.groupby(['has_readme', 'repo']).size().reset_index(name='repo_count')
    readthedocs_counts = df_docs_data.groupby(['has_readthedocs', 'repo']).size().reset_index(name='repo_count')

    def create_figures(counts: pd.DataFrame, path_key: str):
        _fig_treemap = px.treemap(
            counts,
            path=[path_key, 'repo'],
            values='repo_count',
        )
        _fig_sunburst = px.sunburst(
            counts,
            path=[path_key, 'repo'],
            values='repo_count',
        )
        return _fig_treemap, _fig_sunburst

    # List of tuples containing the data and titles for each figure
    figures_data = [
        (readme_counts, 'has_readme', 'Has README file'),
        (readthedocs_counts, 'has_readthedocs', 'Uses ReadTheDocs')
    ]

    # Loop through the list to create figures and add traces
    for _counts, _path_key, title in figures_data:
        fig_treemap, fig_sunburst = create_figures(counts=_counts, path_key=_path_key)

        fig = go.Figure()
        fig.add_trace(fig_treemap.data[0])
        fig.add_trace(fig_sunburst.data[0])
        fig.data[1].visible = False

        fig.update_layout(
            title=title,
            updatemenus=[
                {
                    "buttons": [
                        {
                            "label": "Treemap",
                            "method": "update",
                            "args": [{"visible": [True, False]}],
                        },
                        {
                            "label": "Sunburst",
                            "method": "update",
                            "args": [{"visible": [False, True]}],
                        },
                    ],
                    "direction": "down",
                    "showactive": True,
                }
            ]
        )
        fig.show()
