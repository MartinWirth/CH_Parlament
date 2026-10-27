"""Local environment loading tests."""
import os
from app import config


def test_loads_env_from_project_root(monkeypatch, tmp_path):
    package = tmp_path / 'app'
    package.mkdir()
    monkeypatch.setattr(config, '__file__', str(package / 'config.py'))
    (tmp_path / '.env').write_text('CHP_TEST_SETTING=loaded\nCHP_TEST_PRECEDENCE=file\n', encoding='utf-8')
    monkeypatch.delenv('CHP_TEST_SETTING', raising=False)
    monkeypatch.setenv('CHP_TEST_PRECEDENCE', 'environment')
    config.load_environment()
    assert os.environ['CHP_TEST_SETTING'] == 'loaded'
    assert os.environ['CHP_TEST_PRECEDENCE'] == 'environment'
    monkeypatch.delenv('CHP_TEST_SETTING', raising=False)
