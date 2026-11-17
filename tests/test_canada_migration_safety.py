"""Canada research must preserve saved positions without enabling paper fills."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, PaperTradingTrade, StrategySignalRun
from src.platform.persistence.migrations import MIGRATIONS, _m138_investment_research, _m139_retire_session_legacy


@pytest.mark.parametrize('market', ['TSX', 'TSXV', 'tsx', 'UNKNOWN'])
def test_nonexecuting_markets_cannot_manually_fill_or_borrow_cn_quotes(monkeypatch, market):
    from src.modules.paper_trading import paper_trading_engine as module
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(module, '_is_trading_time', lambda _: True)
    quotes = Mock(side_effect=AssertionError('Unsupported execution must not fetch quotes'))
    monkeypatch.setattr(module, 'md_quote_rows', quotes)
    try:
        with factory() as db:
            db.add(PaperTradingAccount(id=1, enabled=True, initial_capital=1000, current_capital=1000, peak_capital=1000))
            position = PaperTradingPosition(stock_symbol='T', stock_market=market, stock_name='Saved row',
                entry_price=10, quantity=2, current_price=10, status='open', opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            db.add(position)
            db.commit()
            identifier = position.id
        service = module.PaperTradingEngine()
        result = service.close_position_manual(identifier)
        assert result['ok'] is False
        assert result['error_code'] == 'paper_trading_market_unsupported'
        assert service._fetch_quotes_map([('T', market)]) == {}
        quotes.assert_not_called()
        with factory() as db:
            assert db.get(PaperTradingPosition, identifier).status == 'open'
            assert db.query(PaperTradingTrade).count() == 0
            assert db.get(PaperTradingAccount, 1).current_capital == 1000
    finally:
        engine.dispose()


def test_research_migrations_keep_existing_canadian_ids_fractions_and_aliases():
    """Full-feature branch needs no renumbered staged currency migrations."""
    assert [(m.version, m.name) for m in MIGRATIONS if m.version >= 138] == [
        (138, 'investment_research_delivery'), (139, 'retire_session_legacy')]
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE stocks (id INTEGER PRIMARY KEY, symbol TEXT, market TEXT, listing_currency TEXT)'))
            db.execute(text('CREATE TABLE positions (id INTEGER PRIMARY KEY, stock_id INTEGER, quantity INTEGER NOT NULL, cost_price REAL, cost_currency TEXT)'))
            db.execute(text("INSERT INTO stocks VALUES (1,'T','TSX','CAD'),(2,'T','TSXV','CAD'),(3,'T','US','USD'),(4,'T',' tsx ','CAD')"))
            db.execute(text("INSERT INTO positions VALUES (11,1,0.125,100,'CAD'),(12,2,2,10,'CAD'),(13,3,3,20,'USD'),(14,4,0.75,30,NULL)"))
            before = {table: db.execute(text(f'SELECT * FROM {table} ORDER BY id')).all() for table in ('stocks', 'positions')}
            for _ in range(2):
                _m138_investment_research(db)
                _m139_retire_session_legacy(db)
            after = {table: db.execute(text(f'SELECT * FROM {table} ORDER BY id')).all() for table in ('stocks', 'positions')}
            assert after == before
            assert db.execute(text('SELECT quantity, typeof(quantity) FROM positions WHERE id=11')).one() == (0.125, 'real')
    finally:
        engine.dispose()


def test_canadian_research_signals_do_not_enter_existing_paper_market_pools(monkeypatch):
    from src.modules.paper_trading import paper_trading_engine as module
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(module, '_is_trading_time', lambda _: True)
    quotes = Mock(side_effect=AssertionError('Canadian signals must not reach paper quote/fill'))
    monkeypatch.setattr(module, 'md_quote_rows', quotes)
    try:
        with factory() as db:
            db.add(PaperTradingAccount(id=1, enabled=True, initial_capital=1000, current_capital=1000, peak_capital=1000))
            for market in ('TSX', 'TSXV'):
                db.add(StrategySignalRun(stock_symbol='T', stock_market=market, stock_name=market,
                    status='active', action='buy', entry_low=9, entry_high=11, rank_score=90,
                    strategy_name='research', strategy_code='research', snapshot_date=datetime(2026, 1, 1).date()))
                db.add(PaperTradingPosition(stock_symbol='OLD', stock_market=market, stock_name=market,
                    entry_price=10, quantity=2, current_price=10, status='open', opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
            db.commit()
        result = module.PaperTradingEngine()._scan_sync()
        assert result['status'] == 'ok'
        assert result['opened'] == result['closed'] == 0
        quotes.assert_not_called()
        with factory() as db:
            assert db.query(PaperTradingPosition).count() == 2
            assert all(row.status == 'open' for row in db.query(PaperTradingPosition))
            assert db.query(PaperTradingTrade).count() == 0
    finally:
        engine.dispose()



@pytest.mark.parametrize('entrypoint', ['runner', 'startup'])
@pytest.mark.parametrize('unknown_name', [None, 'external_currency_schema', ''])
def test_staged_migration_number_collision_is_rejected_without_writes(monkeypatch, entrypoint, unknown_name):
    from src.platform.persistence.migrations import _ensure_schema_table, run_versioned_migrations
    from src.platform.persistence import database
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as db:
            _ensure_schema_table(db)
            db.execute(text('CREATE TABLE stocks (id INTEGER PRIMARY KEY, symbol TEXT, market TEXT, listing_currency TEXT)'))
            db.execute(text('CREATE TABLE positions (id INTEGER PRIMARY KEY, stock_id INTEGER, quantity INTEGER, cost_currency TEXT)'))
            db.execute(text("INSERT INTO stocks VALUES (1,'T','TSX','CAD'),(2,'T','TSXV','CAD')"))
            db.execute(text("INSERT INTO positions VALUES (11,1,0.25,'CAD'),(12,2,0.75,NULL)"))
            for migration in MIGRATIONS:
                staged_name = {138: 'stock_listing_currency', 139: 'position_cost_currency'}.get(migration.version)
                db.execute(text("INSERT INTO schema_migrations(version,name,checksum,success,error) VALUES(:version,:name,:checksum,1,'')"),
                    {'version': migration.version, 'name': (unknown_name if unknown_name is not None else staged_name) if staged_name else migration.name,
                     'checksum': 'historical_staged_currency' if staged_name else migration.checksum})
            before = {table: db.execute(text(f'SELECT * FROM {table}')).all() for table in ('stocks', 'positions', 'schema_migrations', 'sqlite_master')}
        monkeypatch.setattr(database, 'engine', engine)
        with pytest.raises(RuntimeError, match='staged Canada currency migrations|Unrecognized database migration lineage'):
            (database._init_db_once() if entrypoint == 'startup' else run_versioned_migrations(engine))
        with engine.connect() as db:
            after = {table: db.execute(text(f'SELECT * FROM {table}')).all() for table in before}
            assert after == before
    finally:
        engine.dispose()


def test_full_canada_database_migration_runner_is_idempotent_and_preserves_positions():
    from src.platform.persistence.migrations import run_versioned_migrations, has_pending_migrations
    engine = create_engine('sqlite://')
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as db:
            # Optional historical columns are not used to guess a database lineage.
            db.execute(text('ALTER TABLE stocks ADD COLUMN listing_currency TEXT'))
            db.execute(text('ALTER TABLE positions ADD COLUMN cost_currency TEXT'))
            db.execute(text("INSERT INTO stocks (id,symbol,market,name,listing_currency) VALUES (1,'T','TSX','Toronto','CAD'),(2,'T','TSXV','Venture','CAD')"))
            db.execute(text("INSERT INTO positions (id,stock_id,account_id,quantity,cost_price,cost_currency) VALUES (11,1,1,0.125,10,'CAD'),(12,2,1,0.75,20,'CAD')"))
            before = {table: db.execute(text(f'SELECT * FROM {table} ORDER BY id')).all() for table in ('stocks', 'positions')}
        for _ in range(2):
            run_versioned_migrations(engine)
            assert not has_pending_migrations(engine)
            with engine.connect() as db:
                assert {table: db.execute(text(f'SELECT * FROM {table} ORDER BY id')).all() for table in before} == before
    finally:
        engine.dispose()
