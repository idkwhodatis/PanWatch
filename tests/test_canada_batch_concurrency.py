"""Current batch isolation with exact Canadian listing identity."""
import asyncio
import threading

from src.modules.market.api import quotes


def test_market_batches_overlap_and_one_provider_failure_does_not_erase_canada(monkeypatch):
    barrier = threading.Barrier(3, timeout=5)
    calls = []

    def collect(symbols, market):
        calls.append((market, symbols))
        barrier.wait()
        if market == 'US':
            raise TimeoutError('fixture provider unavailable')
        return [{'symbol': 'RY', 'current_price': 120 if market == 'TSX' else 5,
                 'currency': 'CAD', 'security_type': 'EQUITY',
                 'source': 'fixture', 'source_timestamp': '2026-11-17T00:00:00Z',
                 'delayed': True}]

    monkeypatch.setattr(quotes, 'md_quote_rows', collect)
    request = quotes.QuoteBatchRequest(items=[
        quotes.QuoteItem(symbol='RY.TO', market='TSX'),
        quotes.QuoteItem(symbol='RY', market='TSX'),
        quotes.QuoteItem(symbol='RY', market='TSXV'),
        quotes.QuoteItem(symbol='RY', market='US'),
    ])
    rows = asyncio.run(quotes.get_quotes_batch(request))
    assert sorted(calls) == [('TSX', ['RY']), ('TSXV', ['RY']), ('US', ['RY'])]
    assert [(row['symbol'], row['market'], row['current_price']) for row in rows] == [
        ('RY.TO', 'TSX', 120), ('RY', 'TSX', 120), ('RY', 'TSXV', 5), ('RY', 'US', None),
    ]
    assert all(row['currency'] == 'CAD' and row['is_delayed'] for row in rows[:3])
