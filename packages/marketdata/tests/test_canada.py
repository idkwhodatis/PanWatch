from datetime import datetime, timezone
import pytest
from marketdata.symbol import Symbol
from marketdata.vendors import canada

@pytest.mark.parametrize('raw,market,code,provider', [('ZSP.TO','TSX','ZSP','ZSP.TO'),('REI.UN.TO','TSX','REI-UN','REI-UN.TO'),('BBD.B','TSX','BBD-B','BBD-B.TO'),('RCK.V','TSXV','RCK','RCK.V')])
def test_identity(raw,market,code,provider):
    symbol=Symbol.parse(raw,market)
    assert symbol.code==code and symbol.to_yfinance()==provider

@pytest.mark.parametrize('raw,market', [('RY.TO','US'),('RCK.V','TSX'),('ZSP.TO','TSXV'),('../etc','TSX')])
def test_conflict(raw,market):
    with pytest.raises(ValueError): Symbol.parse(raw,market)

def chart(symbol='ZSP.TO', exchange='TOR', kind='ETF', currency='CAD'):
    return {'chart':{'result':[{'meta':{'symbol':symbol,'exchangeName':exchange,'instrumentType':kind,'currency':currency,'regularMarketTime':1791576000,'regularMarketPrice':23},'timestamp':[1791489600,1791576000], 'indicators':{'quote':[{'open':[20,22],'high':[22,24],'low':[19,21],'close':[21,23],'volume':[100,200]}],'adjclose':[{'adjclose':[10,11]}]}}]}}

@pytest.mark.parametrize('symbol,market,exchange,kind', [('RY','TSX','TOR','EQUITY'),('ZSP','TSX','TOR','ETF'),('REI-UN','TSX','TOR','EQUITY'),('RCK','TSXV','VAN','EQUITY')])
def test_quotes_and_history(monkeypatch,symbol,market,exchange,kind):
    s=Symbol.parse(symbol,market)
    monkeypatch.setattr(canada,'market_get',lambda *a,**kw:chart(s.to_yfinance(),exchange,kind))
    q=canada.CanadaQuoteVendor().fetch([s],{})[0]
    assert q.symbol==symbol and q.market==market and q.currency=='CAD'
    assert q.delayed and q.source_timestamp.tzinfo and q.prev_close==21
    bars=canada.CanadaKlineVendor().fetch([s],{'days':2})
    assert [b.close for b in bars]==[21,23]  # raw OHLC, never adjusted close alone

@pytest.mark.parametrize('changes', [{'symbol':'ZSP'}, {'exchange':'NYQ'}, {'kind':'MUTUALFUND'}, {'currency':'CNY'}])
def test_mismatched_metadata_rejected(monkeypatch,changes):
    monkeypatch.setattr(canada,'market_get',lambda *a,**kw:chart(**changes))
    assert canada.CanadaQuoteVendor().fetch([Symbol.parse('ZSP','TSX')],{}) == []

def test_search_accepts_etf_reit_and_venture_excludes_nav(monkeypatch):
    rows=[{'symbol':s,'exchange':e,'quoteType':t} for s,e,t in [('ZSP.TO','TOR','ETF'),('REI-UN.TO','TOR','EQUITY'),('RCK.V','VAN','EQUITY'),('NAV.TO','TOR','MUTUALFUND'),('RY','NYQ','EQUITY')]]
    monkeypatch.setattr(canada,'market_get',lambda *a,**kw:{'quotes':rows})
    assert [(r['symbol'],r['market']) for r in canada.search_canadian('test')]==[('ZSP','TSX'),('REI-UN','TSX'),('RCK','TSXV')]


def test_bad_symbol_does_not_drop_good_batch_quotes(monkeypatch):
    def fetch(url,**kw):
        if 'BAD.TO' in url: return {'chart': {'result': None}}
        return chart('RY.TO' if 'RY.TO' in url else 'ZSP.TO')
    monkeypatch.setattr(canada,'market_get',fetch)
    rows=canada.CanadaQuoteVendor().fetch([Symbol.parse(s,'TSX') for s in ['RY','BAD','ZSP']],{})
    assert [q.symbol for q in rows]==['RY','ZSP']

@pytest.mark.parametrize('raw', ['ENB.PR.V', 'ENB.PR.V.TO', 'ENB-PV.TO'])
def test_preferred_series_v_preserves_tsx_identity(raw):
    symbol = Symbol.parse(raw, 'TSX')
    assert symbol.code == 'ENB-PV'
    assert symbol.to_yfinance() == 'ENB-PV.TO'

@pytest.mark.parametrize('raw,market', [('ENB.PR.V', None), ('ENB.PR.V', 'TSXV'), ('ENB.PR.A.V', 'TSXV'), ('ENB.PF.V', 'TSX'), ('ENB.PF.A.TO', 'TSX')])
def test_preferred_ambiguity_and_unsupported_notation_fail_closed(raw, market):
    with pytest.raises(ValueError):
        Symbol.parse(raw, market)


def test_preferred_series_a_provider_notation():
    assert Symbol.parse('ENB.PR.A', 'TSX').to_yfinance() == 'ENB-PA.TO'


@pytest.mark.parametrize('offset', [301, 3600, 86400])
def test_future_provider_timestamp_is_rejected_before_quotes_and_bars(monkeypatch, offset):
    now = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(canada, 'datetime', Clock)
    payload = chart()
    payload['chart']['result'][0]['meta']['regularMarketTime'] = now.timestamp() + offset
    monkeypatch.setattr(canada, 'market_get', lambda *args, **kwargs: payload)
    symbol = Symbol.parse('ZSP', 'TSX')
    assert canada.CanadaQuoteVendor().fetch([symbol], {}) == []
    with pytest.raises(canada.VendorError, match='future'):
        canada.CanadaKlineVendor().fetch([symbol], {})


def test_provider_clock_skew_tolerance_does_not_relabel_delayed_quotes(monkeypatch):
    payload = chart()
    payload['chart']['result'][0]['meta']['regularMarketTime'] = datetime.now(timezone.utc).timestamp() + 60
    monkeypatch.setattr(canada, 'market_get', lambda *args, **kwargs: payload)
    result = canada.CanadaQuoteVendor().fetch([Symbol.parse('ZSP', 'TSX')], {})
    assert len(result) == 1 and result[0].delayed is True


@pytest.mark.parametrize('previous', [0, -1, -1e308])
@pytest.mark.parametrize('from_series', [False, True])
def test_nonpositive_previous_close_is_unavailable(monkeypatch, previous, from_series):
    payload = chart()
    row = payload['chart']['result'][0]
    row['meta']['previousClose'] = previous
    if from_series:
        row['indicators']['quote'][0]['close'][0] = previous
    else:
        row['timestamp'] = []
    monkeypatch.setattr(canada, 'market_get', lambda *args, **kwargs: payload)
    result = canada.CanadaQuoteVendor().fetch([Symbol.parse('ZSP', 'TSX')], {})[0]
    assert result.current_price == 23
    assert result.prev_close is None
    assert result.change_amount is None and result.change_pct is None


@pytest.mark.parametrize('from_series', [False, True])
def test_derived_change_overflow_is_unavailable(monkeypatch, from_series):
    payload = chart()
    row = payload['chart']['result'][0]
    row['meta'].update(regularMarketPrice=1e308, previousClose=1e-308)
    if from_series:
        row['indicators']['quote'][0]['close'][0] = 1e-308
    else:
        row['timestamp'] = []
    monkeypatch.setattr(canada, 'market_get', lambda *args, **kwargs: payload)
    result = canada.CanadaQuoteVendor().fetch([Symbol.parse('ZSP', 'TSX')], {})[0]
    assert result.current_price == 1e308 and result.prev_close == 1e-308
    assert result.change_amount == 1e308 and result.change_pct is None


def test_unrepresentable_integer_is_not_a_number():
    assert canada._number(10 ** 1000) is None
