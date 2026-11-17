"""Real mandatory graph storage retains the dated, reference-only currency snapshot.

Model and market transports are controlled, no real portfolio or notification.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_investment_research_integration import host, configure, collected, make_context
from src.modules.automation.base import AccountInfo, PortfolioInfo, PositionInfo
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.research import portfolio_currency
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import AgentContextRun, AnalysisHistory


@pytest.mark.parametrize('market', [MarketCode.CN, MarketCode.TSX, MarketCode.TSXV])
@pytest.mark.parametrize('incomplete', [False, True])
def test_successful_premarket_graph_persists_reference_valuation(host, monkeypatch, market, incomplete):
    engine, _ = host
    agent = PremarketOutlookAgent()
    configure(engine, agent.name)
    context = make_context(keys=((market, 'T'),))
    context.portfolio = PortfolioInfo([AccountInfo(1, 'Synthetic', 1000, [
        PositionInfo(1, 'Synthetic', 1, 'T', 'Fixture', market, 10, 100)])])
    monkeypatch.setattr(portfolio_currency, '_reference_fx', lambda *a, **k: (
        (None, None) if incomplete else (5.0, datetime.now(timezone.utc).date().isoformat())))
    async def canadian_collected(scoped):
        data = await collected(scoped)
        for pack in data['signal_packs'].values():
            pack.quote.currency = 'CAD'
            pack.quote.source_timestamp = datetime.now(timezone.utc)
            pack.quote.delayed = True
            pack.quote.source = 'controlled_canada'
        return data
    monkeypatch.setattr(agent, 'collect', canadian_collected)
    result = asyncio.run(agent.run(context))
    assert result.raw_data['engine'] == 'langgraph'
    with Session(engine) as db:
        saved_context = db.scalars(select(AgentContextRun)).one()
        history = db.scalars(select(AnalysisHistory)).one()
        assert history.content == result.content
        if market == MarketCode.CN:
            assert 'portfolio_valuation' not in result.raw_data
            assert 'portfolio_valuation' not in saved_context.context_payload
        else:
            valuation = result.raw_data['portfolio_valuation']
            assert saved_context.context_payload['portfolio_valuation'] == valuation
            assert history.raw_data['portfolio_valuation'] == valuation
            assert valuation['usage'] == 'reference_only'
            assert valuation['buying_power_verified'] is False
            assert valuation['total_cost_cny'] == (None if incomplete else 5000)
            assert valuation['positions'][f'{market.value}:T']['currency'] == 'CAD'
            assert valuation['valuation_incomplete'] is incomplete


def test_canadian_prior_plan_uses_toronto_day_not_application_day(host):
    from src.modules.research.orchestration.evidence import prior_plan
    engine, _ = host
    cutoff = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)  # July 1 in Toronto
    with Session(engine) as db, db.begin():
        db.add(AnalysisHistory(agent_name='premarket_outlook', stock_symbol='*',
            analysis_date='2026-07-01', title='Previous Toronto session', content='Reference only',
            raw_data={'suggestions': {'TSX:T': {'outlook': 'neutral', 'action': 'watch', 'triggers': ['verified source'], 'account': 'must not leak'}}},
            created_at=datetime(2026, 7, 1, 12), updated_at=datetime(2026, 7, 1, 12)))
    result = asyncio.run(prior_plan('TSX:T', cutoff, 'Asia/Shanghai'))
    assert result == {'analysis_date': '2026-07-01', 'outlook': 'neutral', 'action': 'watch', 'triggers': ['verified source']}
    assert asyncio.run(prior_plan('TSXV:T', cutoff, 'Asia/Shanghai')) is None


@pytest.mark.parametrize('market', ['TSX', 'TSXV'])
def test_presented_canadian_report_keeps_toronto_analysis_day(market):
    from pan_investment_research import ResearchInput, ResearchResult, Security
    from src.modules.research.orchestration.service import present
    prepared = ResearchInput(run_id='toronto', as_of=datetime(2026, 7, 2, 1, tzinfo=timezone.utc),
        securities=[Security(key=f'{market}:T')], evidence=[], portfolio={})
    research = ResearchResult(run_id='toronto', status='skipped', completion='skipped',
        recommendations=[], artifacts=[])
    result = present(PremarketOutlookAgent(), make_context(), prepared, research)
    assert result.raw_data['analysis_date'] == '2026-07-01'
