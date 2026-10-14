"""Capability/configuration contract; no provider network calls."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.modules.market.api.extensions import router
from src.platform.marketdata.extensions import extension_status, require_extension, set_extension_enabled
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AppSettings


@pytest.fixture
def db():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_default_off_read_is_pure(db):
    state = extension_status(db, 'canada')
    assert state['enabled'] is False
    assert state['exchanges'] == ['TSX', 'TSXV']
    assert not state['capabilities']['analysis']
    assert not state['capabilities']['scanning']
    assert not state['capabilities']['paper_trading']
    assert db.query(AppSettings).count() == 0


def test_flag_persists_and_disable_preserves_unrelated_settings(db):
    db.add(AppSettings(key='unrelated', value='keep'))
    db.commit()
    assert set_extension_enabled(db, 'canada', True)['enabled'] is True
    db.expire_all()
    assert extension_status(db, 'canada')['enabled'] is True
    set_extension_enabled(db, 'canada', True)
    assert db.query(AppSettings).count() == 2
    assert set_extension_enabled(db, 'canada', False)['enabled'] is False
    assert db.query(AppSettings).filter_by(key='unrelated').one().value == 'keep'


def test_unavailable_or_disabled_is_never_authorized(db):
    with pytest.raises(HTTPException) as error:
        require_extension(db, 'canada', 'quotes')
    assert error.value.status_code == 403
    set_extension_enabled(db, 'canada', True)
    for capability in ('analysis', 'scanning', 'paper_trading', 'unknown'):
        with pytest.raises(HTTPException) as error:
            require_extension(db, 'canada', capability)
        assert error.value.status_code == 409


def test_unknown_extension_has_no_write(db):
    with pytest.raises(HTTPException) as error:
        set_extension_enabled(db, 'not-canada', True)
    assert error.value.status_code == 404
    assert db.query(AppSettings).count() == 0


def test_invalid_persisted_flag_fails_closed(db):
    db.add(AppSettings(key='market_extension_canada_enabled', value='yes'))
    db.commit()
    assert extension_status(db, 'canada')['enabled'] is False


def test_http_contract_requires_boolean_and_roundtrips(db):
    app = FastAPI()
    app.include_router(router, prefix='/api/market-extensions')
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get('/api/market-extensions').json()[0]['enabled'] is False
        for value in ('false', 1, None):
            assert client.patch('/api/market-extensions/canada', json={'enabled': value}).status_code == 422
        assert client.patch('/api/market-extensions/canada', json={'enabled': True}).json()['enabled'] is True
        assert client.get('/api/market-extensions').json()[0]['enabled'] is True
        assert client.patch('/api/market-extensions/canada', json={'enabled': False}).json()['enabled'] is False


def test_production_mount_is_authenticated():
    # Keep this check independent from application startup/network providers.
    import ast
    from pathlib import Path
    tree = ast.parse(Path('src/bootstrap/application.py').read_text())
    mounts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == 'include_router'
              and any(k.arg == 'prefix' and isinstance(k.value, ast.Constant)
                      and k.value.value == '/api/market-extensions' for k in node.keywords)]
    assert len(mounts) == 1
    assert any(k.arg == 'dependencies' and isinstance(k.value, ast.Name)
               and k.value.id == 'protected' for k in mounts[0].keywords)


def test_generic_setting_cannot_enable_extension(db):
    from src.modules.administration.api.settings import SettingUpdate, update_setting
    for key in ('market_extension_canada_enabled', 'market_extension_unknown_enabled'):
        with pytest.raises(HTTPException) as error:
            update_setting(key, SettingUpdate(value='true'), db)
        assert error.value.status_code == 400
        assert error.value.detail['code'] == 'extension_setting_reserved'
    assert db.query(AppSettings).count() == 0
    assert not extension_status(db, 'canada')['enabled']


def test_template_settings_allowlist_excludes_extensions():
    from src.modules.automation.api.templates import _SETTINGS_KEYS
    assert not any(key.startswith('market_extension_') for key in _SETTINGS_KEYS)
