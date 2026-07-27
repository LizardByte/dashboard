# standard imports
from pathlib import Path


TEMPLATE_DIR = Path(__file__).parents[2] / 'gh-pages-template'


def test_dashboard_script_path_uses_site_base_url():
    """Keep the dashboard script path relative to Jekyll's configured project-site base URL."""
    template = (TEMPLATE_DIR / 'index.html').read_text(encoding='utf-8')

    assert '  - /assets/js/dashboard.js' in template
    assert '  - /dashboard/assets/js/dashboard.js' not in template
