# Canadian listed securities

This independent feature adds TSX and TSX Venture (`TSXV`) holdings, watchlists,
security search, quotes/history, valuation, price alerts and analysis inputs.
Exchange-listed ETFs are supported, alongside stocks, listed REITs, trusts and
other exchange-listed funds. Non-exchange mutual-fund NAV flows, Canadian paper
trading, discovery/factor scanning, futures, crypto and bonds are outside scope.

## Market categories in the interface

Watchlist filters, add-stock/add-holding dialogs, and the assistant stock picker
show one **Canada** category alongside Mainland China, Hong Kong, and US. Canada
searches both TSX and TSX Venture; the watchlist tab counts and includes both.
The category is presentation-only, not a new API market or stored exchange.
Search results, holdings, quotes, research routes, and exchange calendars retain
their original `TSX` / `TSXV` identity, including `.TO` / `.V` entry support.
Existing data needs no migration. Exchange badges still disambiguate identical
ticker strings. If one exchange search fails, results from the other remain
available with a warning; changing the query/category or closing the picker
discards older in-flight results.

## Identity and provider boundaries

Persist the exchange separately from the symbol: `TSX:ZSP`, `TSX:REI-UN`,
`TSXV:RCK`. Provider `.TO` / `.V` suffixes are normalized at entry. Unit/class
punctuation (`REI.UN`, `BBD.B`) maps to Yahoo hyphens. Explicit incompatible
market/suffix pairs are rejected, including `US:RY.TO`.
The single/batch quote HTTP APIs resolve these aliases to canonical provider
identities while echoing each requested symbol in the response. Batches retain
input order and duplicates; invalid/conflicting identities return HTTP 400.

`yahoo_canada` is a dedicated, enabled-by-default public Yahoo chart/search
adapter, registered only for TSX/TSXV. It accepts exact matching Canadian
exchange identity, EQUITY/ETF type, CAD/USD currency and a source timestamp.
REITs/trusts classified EQUITY are retained; MUTUALFUND NAV products are excluded.
Quotes are delayed/best-effort, not an exchange-feed SLA. Missing identity,
currency or source time means unavailable. No US namesake fallback is allowed.
Daily history consistently uses raw OHLC (never adjusted close mixed with raw
open/high/low). Search is provider-limited, not a complete exchange directory.
Canadian enrichment failure cannot discard other-market search results.

## Valuation and automation

- Canadian quantities may be finite positive fractions. Existing CN/HK/US whole
  quantity behavior is preserved. Template import/export retains fractions.
- CAD/CNY and Canadian USD/CNY are verified independently with source dates and
  bounded caches. The legacy US fallback rate is never used for Canadian USD
  classes. Metadata-only loading supports later quote/FX reconciliation.
- Unknown quote currency, FX or price produces explicit incomplete valuation.
  Partial P&L excludes unvalued costs, avoiding fabricated complete losses.
  DB-only overview never treats nominal CAD cost as CNY. Full portfolio analysis
  and benchmark/attribution cannot silently use incomplete totals.
- Scheduled daily, premarket and intraday research use the current mandatory
  investment-research graph, not the retired prompt pipelines, and preserve Canadian native
  currency, quote source/time and delay. Cross-currency costs and concentration
  use the same bounded, dated reference FX as portfolio valuation; missing
  currency/FX leaves totals and risk budgets unavailable. CNY cash is labelled
  separately, and legacy synthetic FX is not used in these Canadian contexts.
  Reference FX is informational valuation evidence only: it does not verify
  settlement, executable prices or available buying power, and cannot authorize orders.
- Toronto timezone handles DST. Only the verified TMX 2026 calendar is bundled,
  including December24 13:00 close. Unknown future weekdays fail closed for
  automatic execution. US-only holidays are not Canadian closures. Alert-health
  quotas and execution use the same Toronto trading date, including DST.
- Canadian alerts require a known currency and source time within30 minutes.
  Messages retain source, currency and delayed timestamp information. Historical
  dry runs remain separate from automatic live triggers.

## Analysis identity and unsupported data

Market-qualified keys prevent dual-listed tickers from mixing quotes, technical
series, holdings, history, active traces or cache entries. Bare keys remain only
for unique legacy symbols. Ambiguous bare-symbol model suggestions are not saved.
Canadian history IDs (`TSX:RY`) remain qualified across modal/detail lookups,
comparison, trace deduplication and startup backfill boundaries.

TradingAgents receives exact `.TO` / `.V` symbols and market-scoped portfolio
positions. Canadian run, history-save and cache dates use Toronto. Metadata
includes verified quote currency/type/time; cash is CNY and cannot be compared to
native prices without FX. ETF corporate fundamentals and unsupported NAV,
holdings, distributions, Canadian filings or benchmarks must remain unavailable,
not inferred from US issuers or ordinary corporate assumptions. No real LLM or
external notification delivery is implied by deterministic tests.

The runtime dependency remains the current upstream official TradingAgents v0.7.0 pin.
Canada-specific provider symbols, metadata, Toronto dates and history identity
are handled by this branch's PanWatch adapter. The optional user-fork dependency
and modern-layout compatibility are a separate, independently mergeable branch.
Official upstream data coverage remains best-effort; no full Canadian fundamental
or live-model coverage is implied by local wrapper contracts.

## Low-pain upstream synchronization

Keep this feature as its own commit/PR. New provider, yearly calendar and identity
helpers are isolated in `vendors/canada.py`, `canada_calendar.py`,
`instrument_identity.py`.
Recheck these small integration surfaces after upstream sync:

1. Package/host market enums, symbol normalization, vendor registry and datasource
   seeds. Preserve user enable/disable settings when reconciling seeds.
2. Stock search/quote APIs, market-qualified maps and timestamp/currency metadata.
3. Holdings, overview and templates: preserve fractional quantities, nullable
   verified FX and partial-P&L semantics.
4. SignalPack/context/report lookups, TradingAgents ticker/history/trace keys,
   Toronto dates, official v0.7.0 adapter contracts and CN/HK/US behavior.
5. Frontend selectors, bilingual labels, precision, delayed/incomplete warnings
   and deep-analysis qualified history navigation.

`.github/workflows/canadian-markets.yml` runs read-only PR regression coverage:
marketdata package, Canadian provider/identity/calendar/valuation, templates,
TradingAgents boundaries, frontend tests, guards and production build.
Live public probes and authenticated isolated API checks are separate from mocks.

## Recovery provenance

The earlier temporary environment was lost. This checkout was reconstructed from
retained implementation/review context on2026-10-10, not recovered byte-for-byte.
Earlier test counts do not certify this reconstructed source. Fresh logs,
patches and a Git bundle are retained outside the repository in the persistent
workspace. Final validation records distinguish fresh checks from historical
public-data evidence and explicitly disclose unexecuted live-model/UI checks.

## Sources

- TMX calendar: https://www.tsx.com/en/trading/calendars-and-trading-hours/calendar
- TMX hours: https://www.tsx.com/en/trading/calendars-and-trading-hours/trading-hours
- FX API: https://frankfurter.dev/v1/

### Native preferred-share notation

With an explicit TSX market, `ENB.PR.V` and `ENB.PR.V.TO` normalize to
`ENB-PV` (Yahoo `ENB-PV.TO`); the final V is a preferred series, not TSXV.
An unqualified native preferred code requires an explicit TSX market.
Unsupported `.PF.<series>` notation must use an exact Yahoo symbol rather
than a guessed conversion. Assistant market tools use the same canonical
listing identity as stock creation and the quote provider.

## Current-architecture migration boundary

This full-function branch is distinct from the default-off, native-only Canada
extension slices. It retains the established exchange identifiers and current
canonical stock/position records; it does not import the staged extension's
listing/cost-currency migrations numbered 138 and 139. Those numbers now belong
to upstream investment-research delivery and retirement of legacy sessions,
which remain unchanged. No positions are removed or silently merged.
A database already stamped with the experimental staged extension's
`stock_listing_currency` / `position_cost_currency` migrations must not be
silently treated as a full-function database: its saved cost-currency contract
is different. Startup rejects that known ledger before writes with an
explicit migration-required error. Preserve a backup and reconcile native cost
currency before a separately reviewed staged-to-full conversion; merely
renumbering the ledger or dropping the columns is not a supported migration.

Frontend integration keeps upstream's extracted account/position dialogs and
analysis reader while preserving one visible Canada category and exact TSX/TSXV
selection. Quote batches keep upstream's concurrent per-market timeout isolation
and Canadian alias normalization, so an unavailable provider lane does not
erase valid quotes from another exchange.

Canadian research, monitoring and manual holdings are not permission to execute
paper or broker orders. Existing unsupported-market execution checks must stay
closed, including old or manually seeded paper records. Fresh validation and
remaining real-browser/provider limitations belong to the head-specific report,
not to historical test logs.
