# standard imports
import importlib
import os


def test_src_init_sets_paths_and_loads_dotenv(monkeypatch):
    calls = []
    monkeypatch.setattr('dotenv.load_dotenv', lambda: calls.append(True))

    import src
    reloaded = importlib.reload(src)

    assert calls
    assert reloaded.BASE_DIR.endswith('gh-pages')
    assert reloaded.TEMPLATE_DIR.endswith('gh-pages-template')
    assert os.path.isabs(reloaded.BASE_DIR)
    assert os.path.isabs(reloaded.TEMPLATE_DIR)
