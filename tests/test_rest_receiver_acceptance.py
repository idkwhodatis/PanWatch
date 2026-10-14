"""Real loopback API + TLS receiver acceptance; all data and credentials are synthetic."""
import asyncio
import json
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import uvicorn

from test_monitoring_delivery import factory, hit
from test_rest_notifications import certificate
from src.modules.market.alert_delivery import AlertDeliveryWorker, retry_delivery
from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    AgentConfig, AppSettings, NotifyChannel, PriceAlertDelivery, PriceAlertRule, Stock,
)


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Override the global no-op: the only receiver is a disposable TLS listener.
    pass


@contextmanager
def receiver(path, tls, token):
    """Generic JSON sink: dedupe uses the HTTP header, no proprietary envelope."""
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE accepted (key TEXT PRIMARY KEY, body TEXT)')
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            authenticated = self.headers.get('Authorization') == 'Bearer ' + token
            key = self.headers.get('Idempotency-Key')
            status, duplicate = 401, False
            if authenticated:
                with sqlite3.connect(path) as db:
                    inserted = db.execute('INSERT OR IGNORE INTO accepted VALUES (?, ?)',
                                          (key, body.decode())).rowcount
                duplicate = not inserted
                status = 503 if self.path == '/retry' and inserted else 202
            # Save sanitized observations only. Never store Authorization.
            calls.append({'path': self.path, 'authenticated': authenticated,
                          'key': key, 'payload': json.loads(body),
                          'status': status, 'duplicate': duplicate})
            if authenticated and self.path == '/timeout':
                time.sleep(1.4)  # exceeds the configured whole-attempt deadline
            try:
                self.send_response(status)
                self.end_headers()
            except (OSError, ConnectionError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def live_api(app):
    # Production routing/auth over a real socket. Lifespan is off to prevent
    # unrelated scheduler/market-data jobs in this isolated acceptance test.
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    address = sock.getsockname()
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_config=None,
                                          access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{address[1]}',
                          trust_env=False, timeout=10) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def test_live_authenticated_receiver_configuration_test_report_alert_and_failures(
        factory, certificate, tmp_path, monkeypatch, caplog):
    import server
    from src.modules.administration.api import auth

    token = secrets.token_urlsafe(24)
    password = secrets.token_urlsafe(24)
    tls, pem = certificate
    monkeypatch.setenv('REST_ACCEPTANCE_TOKEN', token)
    monkeypatch.setenv('REST_ACCEPTANCE_CA', pem)
    monkeypatch.setattr(server, 'SessionLocal', factory)
    monkeypatch.setattr(auth, 'SessionLocal', factory)

    def qa_db():
        with factory() as db:
            yield db

    monkeypatch.setitem(server.app.dependency_overrides, get_db, qa_db)
    with factory() as db:
        db.add_all([
            AppSettings(key='auth_username', value='receiver-qa'),
            AppSettings(key='auth_password_hash', value=auth.hash_password(password)),
            AppSettings(key='ui_language', value='en-US'),
            AppSettings(key='notify_retry_attempts', value='2'),
            AppSettings(key='notify_retry_backoff_seconds', value='0'),
            AgentConfig(name='daily_report', display_name='Daily report', kind='workflow', config={}),
        ])
        db.commit()

    path = tmp_path / 'receiver.db'
    with receiver(path, tls, token) as (origin, calls), live_api(server.app) as client:
        assert client.get('/api/channels').status_code == 401
        login = client.post('/api/auth/login', json={'username': 'receiver-qa', 'password': password})
        assert login.status_code == 200
        client.headers['Authorization'] = 'Bearer ' + login.json()['data']['token']
        config = {'endpoint_url': origin + '/events', 'token_env': 'REST_ACCEPTANCE_TOKEN',
                  'tls_ca_env': 'REST_ACCEPTANCE_CA', 'timeout_seconds': '1'}
        created = client.post('/api/channels', json={
            'name': 'Acceptance receiver', 'type': 'custom_rest', 'config': config,
        })
        assert created.status_code == 200
        channel_id = created.json()['data']['id']
        listed = client.get('/api/channels')
        assert token not in listed.text and pem not in listed.text
        tested = client.post(f'/api/channels/{channel_id}/test')
        assert tested.status_code == 200
        assert 'downstream delivery is not verified' in tested.json()['data']['message']
        assert calls[-1]['authenticated'] and calls[-1]['status'] == 202

        assigned = client.put('/api/agents/daily_report', json={'notify_channel_ids': [channel_id]})
        assert assigned.status_code == 200
        channels = server.resolve_notify_channels('daily_report')
        # Synthetic existing-report content through production channel routing;
        # no model invocation or claim of a real financial report generation.
        result = asyncio.run(server._build_notifier(channels).notify_with_result(
            'Synthetic daily report', '**Receiver acceptance**\nNo user holdings.',
            message_id='receiver-acceptance-report'))
        assert result == {'success': True}
        assert calls[-1]['payload']['message'] == '**Receiver acceptance**\nNo user holdings.'

        # Receiver commits then returns 503. The durable alert worker retries
        # exactly once per drain; receiver-side uniqueness suppresses a duplicate.
        updated = client.put(f'/api/channels/{channel_id}', json={
            'config': config | {'endpoint_url': origin + '/retry'}})
        assert updated.status_code == 200
        with factory() as db:
            stock = Stock(symbol='600519', market='CN', name='Synthetic QA')
            db.add(stock)
            db.flush()
            rule = PriceAlertRule(stock_id=stock.id,
                condition_group={'op': 'and', 'items': [{'type': 'price', 'op': '>=', 'value': 5}]},
                market_hours_mode='always', cooldown_minutes=0, max_triggers_per_day=0,
                notify_channel_ids=[channel_id])
            db.add(rule)
            db.commit()
            rule_id = rule.id
            stock_id = stock.id
        hit(factory, rule_id)
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=1)) == {'processed': 1}
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            assert row.status == 'retry' and row.attempts == 1
            assert row.delivered_at is None
            retry_delivery(db, row.id)
        assert asyncio.run(AlertDeliveryWorker(factory).drain(limit=1)) == {'processed': 1}
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            assert row.status == 'delivered' and row.attempts == 2
        first, second = calls[-2:]
        assert first['status'] == 503 and second['status'] == 202 and second['duplicate']
        assert first['key'] == second['key'] and first['payload'] == second['payload']
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT count(*) FROM accepted WHERE key=?', (first['key'],)).fetchone()[0] == 1

        # An invalid receiver credential fails the ordinary Test API, never a
        # false-positive accepted status and never a retry of a permanent 401.
        monkeypatch.setenv('REST_ACCEPTANCE_TOKEN', 'invalid-synthetic-token')
        count = len(calls)
        rejected = client.post(f'/api/channels/{channel_id}/test')
        assert rejected.status_code == 500 and len(calls) == count + 1
        assert calls[-1]['status'] == 401 and not calls[-1]['authenticated']
        monkeypatch.setenv('REST_ACCEPTANCE_TOKEN', token)

        # Receiver persisted the body but withheld response headers past 1s:
        # sender cannot claim success. Replay with a repaired endpoint retains
        # the durable identity and is acknowledged as an existing record.
        assert client.put(f'/api/channels/{channel_id}', json={
            'config': config | {'endpoint_url': origin + '/timeout'}}).status_code == 200
        with factory() as db:
            row = db.query(PriceAlertDelivery).one()
            # Fresh synthetic delivery, using production enqueue semantics.
            from src.modules.market.alert_delivery import enqueue
            from src.platform.persistence.models import PriceAlertHit
            alert = PriceAlertHit(rule_id=rule_id, stock_id=stock_id, trigger_bucket='timeout')
            db.add(alert)
            db.flush()
            enqueue(db, alert, db.get(PriceAlertRule, rule_id), 'Synthetic QA', '600519')
            db.commit()
            timeout_hit_id = alert.id
        started = time.monotonic()
        asyncio.run(AlertDeliveryWorker(factory).drain(limit=1))
        elapsed = time.monotonic() - started
        assert .8 <= elapsed < 3
        timed_out = calls[-1]
        with factory() as db:
            row = db.query(PriceAlertDelivery).filter_by(hit_id=timeout_hit_id).one()
            assert row.status == 'retry' and row.delivered_at is None
            retry_delivery(db, row.id)
        assert client.put(f'/api/channels/{channel_id}', json={'config': config}).status_code == 200
        asyncio.run(AlertDeliveryWorker(factory).drain(limit=1))
        assert calls[-1]['duplicate'] and calls[-1]['key'] == timed_out['key']
        assert calls[-1]['payload'] == timed_out['payload']
        with factory() as db:
            row = db.query(PriceAlertDelivery).filter_by(hit_id=timeout_hit_id).one()
            assert row.status == 'delivered' and row.attempts == 2
        assert token not in caplog.text
        # Opt-in console evidence is sanitized and reproducible in CI logs.
        print('RECEIVER_ACCEPTANCE ' + json.dumps({
            'transport': 'real HTTPS loopback with private CA and Bearer authentication',
            'api': 'real HTTP loopback with login; scheduler disabled',
            'test': 'receiver acknowledged; downstream unverified',
            'report': 'synthetic content via existing report channel routing',
            'alert_retry': ['retry/1', 'delivered/2'],
            'auth_failure': 'HTTP 401, API 500, no retry',
            'timeout_replay': ['retry/1', 'delivered/2'],
            'timeout_seconds': round(elapsed, 2),
            'requests': calls,
        }, ensure_ascii=False))
