"""Research outbox to real HTTPS receiver, with controlled graph/model inputs."""
import asyncio
import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_investment_research_integration import host, configure, collected, make_context
from test_rest_notifications import certificate, gateway
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.research.orchestration.models import ResearchOutbox, ResearchRun
from src.platform.notifications.notifier import NotifierManager
from src.platform.notifications.notify_policy import NotifyPolicy


@pytest.fixture(autouse=True)
def _suppress_notifications():
    # Real delivery goes only to the disposable loopback receiver below.
    pass


@pytest.mark.parametrize('status', [202, 503, 429])
@pytest.mark.parametrize('channels', [1, 2])
def test_research_https_identity_is_durable_and_never_replayed(host, certificate, monkeypatch, status, channels):
    engine, _ = host
    tls, cert = certificate
    monkeypatch.setenv('RESEARCH_TEST_CA', cert)
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, 'collect', collected)
    context = make_context()
    context.suppress_notify = False
    context.notifier = NotifierManager(NotifyPolicy(retry_attempts=2, retry_backoff_seconds=0))
    with gateway((status,), tls=tls) as (config, calls):
        for i in range(channels):
            context.notifier.add_channel('custom_rest', {
                **config, 'endpoint_url': config['endpoint_url'] + f'/{i}', 'tls_ca_env': 'RESEARCH_TEST_CA',
            })
        first = asyncio.run(agent.run(context))
        second = asyncio.run(agent.run(context))
    assert len(calls) == channels  # no transport retry, nor duplicate run replay
    assert first.raw_data['notified'] is (status == 202)
    assert second.raw_data['notified'] is (status == 202)
    events = [json.loads(body) for _, body in calls]
    with Session(engine) as db:
        rows = db.scalars(select(ResearchOutbox)).all()
        assert len(rows) == channels
        assert {row.status for row in rows} == ({'sent'} if status == 202 else {'unknown'})
        expected = {'research-' + hashlib.sha256(f'{row.run_id}/{row.channel_key}'.encode()).hexdigest() for row in rows}
        run = db.scalars(select(ResearchRun)).one()
        assert {event['timestamp'] for event in events} == {run.as_of.replace('+00:00', 'Z')}
    assert {event['event_id'] for event in events} == expected
    assert {event['delivery_id'] for event in events} == expected
    assert len({headers['Idempotency-Key'] for headers, _ in calls}) == channels


def test_research_timeout_attempt_is_claimed_once(host, monkeypatch):
    from src.platform.notifications import custom_rest
    from src.platform.notifications.webhook_transport import PublishError

    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, 'collect', collected)
    context = make_context()
    context.suppress_notify = False
    context.notifier = NotifierManager(NotifyPolicy(retry_attempts=2, retry_backoff_seconds=0))
    context.notifier.add_channel('custom_rest', {'endpoint_url': 'https://receiver.invalid/events'})
    attempts = []

    async def timeout(config, envelope):
        attempts.append(envelope)
        raise PublishError('timeout', retryable=True)

    monkeypatch.setattr(custom_rest, 'publish_notification', timeout)
    asyncio.run(agent.run(context))
    asyncio.run(agent.run(context))
    assert len(attempts) == 1
    with Session(engine) as db:
        row = db.scalars(select(ResearchOutbox)).one()
        assert row.status == 'unknown'
        expected = 'research-' + hashlib.sha256(f'{row.run_id}/{row.channel_key}'.encode()).hexdigest()
    assert attempts[0]['event_id'] == attempts[0]['delivery_id'] == expected


def test_legacy_research_notifier_keeps_two_argument_contract(host, monkeypatch):
    from types import SimpleNamespace

    engine, _ = host
    agent = DailyReportAgent()
    configure(engine, agent.name)
    monkeypatch.setattr(agent, 'collect', collected)
    calls = []

    async def notify(title, content):
        calls.append((title, content))
        return {'success': True}

    context = make_context()
    context.suppress_notify = False
    context.notifier = SimpleNamespace(notify_with_result=notify)
    first = asyncio.run(agent.run(context))
    asyncio.run(agent.run(context))
    assert first.raw_data['notified'] is True
    assert len(calls) == 1
    with Session(engine) as db:
        assert db.scalars(select(ResearchOutbox)).one().status == 'sent'
