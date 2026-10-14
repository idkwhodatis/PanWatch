# TradingAgents build-time source selection

The recommended path remains `pip install -r requirements.txt` or the standard
PanWatch image. The official TradingAgents 0.5.0 dependency is pinned to
`2d17df8da1536c121e4d7395ac5a5dcec9e96d6f` (the v0.5.0 release commit).

Advanced users can prepare a reproducible dependency manifest from the repository
root, without editing tracked requirements:

```sh
python scripts/prepare-tradingagents-requirements.py --source 0.5.0
# Inspect .requirements-custom.txt before installing.
python scripts/prepare-tradingagents-requirements.py --source 0.5.0 --install
```

Alternatively, supply a credential-free `git+https://host/owner/repo.git@SHA`
with a full 40-character commit SHA. Tags, branches, pip options, local paths,
credentials, query strings and fragments are rejected. An exact package version
does not lock transitive dependencies or authenticate the package publisher.
Pinning and syntax validation do not audit source code; installing a selected
package can execute its build code. Use only sources you have reviewed and trust.

`TRADINGAGENTS_SOURCE` supplies the same input when `--source` is omitted.
For an image built from this checkout:

```sh
docker build --build-arg TRADINGAGENTS_SOURCE=0.5.0 -t panwatch:source-test .
```

Selection happens only during installation or image build. Setting a running
container's environment does not replace its installed package. No UI selector,
automatic upgrade, or runtime package installation is introduced.

To restore the official dependency in a reused virtual environment:

```sh
python scripts/prepare-tradingagents-requirements.py --source "" --install
```

The explicit empty value overrides the environment. `--install` first reinstalls
only TradingAgents without dependencies, then resolves the generated full runtime
manifest. This avoids pip retaining a same-version package from a different source.
It is not transactional: if resolution fails, repair or recreate the virtual
environment before use. Use a fresh virtual environment for evaluation.

## Supported boundary

The only recommended source is the official commit above. Alternate sources must
preserve PanWatch's existing official module, graph, timeout and portfolio APIs.
A structural availability check rejects known incompatible API surfaces before
analysis and explains how to restore the official source. A passing signature
check or successful installation does not prove semantic compatibility or a
successful analysis. Errors elsewhere in dependency imports can still make a
source unavailable.

This slice intentionally does not adapt modern fork layouts or add market,
portfolio, cancellation or LLM-factory behavior. In particular,
`idkwhodatis/TradingAgents@77240b1f2ab2ca5a93af4534edb864c1a0857539` is not a supported
analysis target for this slice. Its advanced capabilities require a separate
scope agreement and compatibility implementation. No arbitrary-fork support is
promised.

Before proposing an alternate source upstream, open an agreed issue describing
the concrete user capability, exact supported commits, maintenance owner and
real image-build/analysis acceptance evidence. Do not infer upstream acceptance
from this tooling or its unit tests. This draft leaves that product decision open.
