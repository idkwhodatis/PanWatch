# Optional Canadian market delivery

This work is prepared locally as independently reviewable changes. It does not
replace the full-feature fork or authorize publishing a branch or PR.

## Stage 1: configuration and capability discovery

Settings → Optional markets exposes a persistent, default-off Canada switch and
an explicit installed-capability list. Both TSX and TSXV belong to the Canada
extension, but retain separate exchange identities. The authenticated API is:

- `GET /api/market-extensions`
- `PATCH /api/market-extensions/canada` with `{"enabled": true|false}`

In this stage **all Canada capabilities are unavailable**. Enabling only stores
a preference; it does not register a provider, modify existing markets, contact a
provider or start jobs. Capability checks distinguish disabled (403), not
installed (409) and unknown extension (404). Invalid persisted flags fail closed.
The UI refresh action verifies ambiguous write failures instead of assuming that
a timed-out request failed to persist. Existing settings and saved records are
not deleted on disable.

Acceptance: fresh database shows disabled and no capabilities; enabling survives
read-back/session restart; disabling preserves unrelated data; invalid/unknown
configuration is rejected; failed saves keep prior visible state and offer
refresh. CN/HK/US registries, valuation and dispatch remain unchanged.

## Stage 2: independently usable quote and daily-bar API

Install only isolated Canada quote/K-line capabilities behind the same switch.
Expose authenticated read-only endpoints with exact TSX/TSXV identity, CAD/USD
currency, instrument type, source time and delayed-data status. Do not extend the
shared execution-market enum. Reject ambiguous/mismatched provider metadata and
unavailable data rather than falling back to a US namesake. A quote or bar API is
usable without watchlist, portfolio, analysis, scanning or paper trading.

Acceptance includes real-provider samples for an ordinary TSX equity, TSXV,
ETF, REIT, share class and USD listing, plus deterministic failure regressions.
A short history must be labelled partial; absence of data is not successful data.

## Stages 3a and 3b: native watchlist, then native holdings

Use canonical Stock/Position records and existing account flows. Do not introduce
a parallel portfolio book simply to meet a review budget. These are separate
usable changes because shared unknown-market fallbacks and currency persistence
need explicit treatment:

- 3a: native search/watchlist and quotes; persist verified currency; reject Canada
  holdings until 3b and reject/exclude advanced workflows. Saved metadata remains
  readable while disabled, without requesting fresh provider data.
- 3b: manual holdings in existing accounts; retain cost currency independently of
  subsequent quote availability; native CAD/USD valuation and separate totals.
  Missing or changed quote currency is unavailable, never silently CNY/USD.
  Existing CNY aggregates must state incomplete scope instead of presenting a
  partial sum as a complete portfolio valuation.

Analysis, scanning, alerts and paper trading remain later proposals. Each change
must pass both repository limits: 20 production files and 800 added + deleted
production lines. Tests, Markdown docs and known lockfiles are excluded by the
actual repository policy classifier; translations and configuration count.
Correctness is not traded for a smaller diff. Any necessary further split or
scope exception must be identified before publication.

## Evidence rules

Each local acceptance report records the exact tested commit, commands and exit
codes, Steps/Expected/Observed/Evidence, plus whether a check was a deterministic
mock, authenticated runtime API, live provider, or browser test. A test pass does
not imply visual QA or live data were checked. Local evidence must be published
or attached accessibly before a future PR can claim delivered acceptance evidence.
