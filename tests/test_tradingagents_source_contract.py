"""Structural checks must fail closed without calling models or market APIs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / 'src/modules/automation/tradingagents/source_contract.py'
spec = importlib.util.spec_from_file_location('source_contract_test', PATH)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def compatible_module():
    def accepts(*args, **kwargs):
        raise AssertionError('Structural checks must never execute upstream code')
    return SimpleNamespace(
        TradingAgentsGraph=SimpleNamespace(__init__=accepts, _get_provider_kwargs=accepts, propagate=accepts),
        Propagator=SimpleNamespace(create_initial_state=accepts, get_graph_args=accepts),
        route_to_vendor=accepts, load_ohlcv=accepts,
        build_verified_market_snapshot=accepts, Position=accepts, PortfolioContext=accepts,
    )


def test_structural_check_does_not_execute_upstream_functions(monkeypatch):
    monkeypatch.setattr(contract, 'import_module', lambda name: compatible_module())
    contract.validate_source_contract()


@pytest.mark.parametrize('attribute', [
    'TradingAgentsGraph.__init__', 'TradingAgentsGraph._get_provider_kwargs',
    'TradingAgentsGraph.propagate', 'Propagator.create_initial_state',
    'Propagator.get_graph_args', 'route_to_vendor', 'load_ohlcv',
    'build_verified_market_snapshot', 'Position', 'PortfolioContext',
])
def test_missing_boundaries_are_explicit(monkeypatch, attribute):
    module = compatible_module()
    owner = module
    parts = attribute.split('.')
    for part in parts[:-1]:
        owner = getattr(owner, part)
    # None also covers inherited object.__init__ after deleting a mock method.
    setattr(owner, parts[-1], None)
    monkeypatch.setattr(contract, 'import_module', lambda name: module)
    with pytest.raises(ValueError, match='Unsupported TradingAgents API') as error:
        contract.validate_source_contract()
    assert attribute in str(error.value)
    assert '--source "" --install' in str(error.value)


def test_modern_only_layout_is_rejected_without_fallback(monkeypatch):
    def importer(name):
        if name == 'tradingagents.dataflows.interface':
            raise ModuleNotFoundError(name)
        return compatible_module()
    monkeypatch.setattr(contract, 'import_module', importer)
    with pytest.raises(ValueError, match='dataflows.interface.route_to_vendor'):
        contract.validate_source_contract()


def test_wrong_signature_is_rejected(monkeypatch):
    module = compatible_module()
    module.TradingAgentsGraph.propagate = lambda self, symbol, date: None
    monkeypatch.setattr(contract, 'import_module', lambda name: module)
    with pytest.raises(ValueError, match='TradingAgentsGraph.propagate'):
        contract.validate_source_contract()
