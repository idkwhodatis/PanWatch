"""Fail early for sources that lack PanWatch's existing official API surface.

This is a structural guard, not an adapter or an analysis certification.
"""
from importlib import import_module
from inspect import signature


def validate_source_contract() -> None:
    checks = (
        ("graph.trading_graph", "TradingAgentsGraph.__init__", (None,),
         {"selected_analysts": [], "debug": False, "config": {}, "callbacks": []}),
        ("graph.trading_graph", "TradingAgentsGraph._get_provider_kwargs", (None,), {}),
        ("graph.trading_graph", "TradingAgentsGraph.propagate", (None, "SYMBOL", "2026-01-01"), {"portfolio": None}),
        ("graph.propagation", "Propagator.create_initial_state", (None, "SYMBOL", "2026-01-01"), {"portfolio_context": ""}),
        ("graph.propagation", "Propagator.get_graph_args", (None,), {"callbacks": []}),
        ("dataflows.interface", "route_to_vendor", ("get_stock_data", "SYMBOL"), {"vendor": "yfinance"}),
        ("dataflows.stockstats_utils", "load_ohlcv", ("SYMBOL", "2026-01-01"), {"fill_gaps": False}),
        ("dataflows.market_data_validator", "build_verified_market_snapshot", ("SYMBOL", "2026-01-01"), {"indicators": None}),
        ("portfolio", "Position", (), {"ticker": "SYMBOL", "quantity": 1, "average_price": 1}),
        ("portfolio", "PortfolioContext", (), {"positions": []}),
    )
    for module_name, attribute, args, kwargs in checks:
        try:
            target = import_module(f"tradingagents.{module_name}")
            for part in attribute.split("."):
                target = getattr(target, part)
            signature(target).bind(*args, **kwargs)
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Unsupported TradingAgents API: {module_name}.{attribute}. "
                "Restore the official source with `python scripts/prepare-tradingagents-requirements.py "
                '--source "" --install`; see docs/tradingagents-source.md.'
            ) from exc
