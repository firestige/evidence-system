# evidence-system

English | [中文](README.zh-CN.md)

evidence-system is the Evidence System of workflow-self-recursive — an optional, separately deployable, loopback-only data service. It accepts supported OTLP facts from Execution, persists truthful causal and factual projections, and exposes committed state through a versioned read-only query API without controlling execution. Execution continues when Evidence or telemetry is unavailable.

Three modules separate the concerns:

- **Observation Admission** decides what may become accepted, resolves stable record identity, detects duplicate/conflict, and coordinates the transaction.
- **Factual Projection** derives owner-scoped causal and factual state with explicit final/lower-bound/unavailable/not-applicable semantics.
- **Query & API** exposes committed state through the sole external read boundary used by BI and Evolution.

The first release runs as one local Evidence API service and one internal PostgreSQL database. It does not host a UI, proxy a presentation tier, or expose PostgreSQL to consumers.

## Developer preview

This repository is part of workflow-self-recursive's architecture-first developer preview for trusted local use by individuals and small teams. The current scaffold is buildable and testable; admission, projection, query, and retention behavior will arrive in later Iteration 4 waves. **THERE WILL BE COMPATIBILITY-BREAKING CHANGES.**

## Development

Python 3.13 and 3.14 are supported; local development defaults to the latest available 3.14 patch. [uv](https://docs.astral.sh/uv/) owns dependency locking and builds.

```sh
make sync         # exact dependency environment from uv.lock
make format       # rewrite Python formatting
make lint         # format check, Ruff lint, and strict mypy
make unit         # small tests, no external services
make integration  # ephemeral PostgreSQL 18 migration and integration test
make deployment   # build and smoke-test the loopback Docker Compose deployment
make build        # wheel and sdist in dist/
make check        # non-container quality/build gate
```

The supported Compose deployment publishes the API only on `127.0.0.1:4318`; PostgreSQL has no host-published port. Runtime startup never applies migrations implicitly.

## Get the source

This repository is normally consumed as a submodule of [workflow-self-recursive](https://github.com/firestige/workflow-self-recursive):

```sh
git clone --recurse-submodules https://github.com/firestige/workflow-self-recursive.git
```

To clone it standalone:

```sh
git clone https://github.com/firestige/evidence-system.git
```

## Documentation

- [Evidence System design](https://github.com/firestige/workflow-self-recursive/blob/main/docs/systems/evidence/evidence-system.md)
- [Evidence implementation baseline](https://github.com/firestige/workflow-self-recursive/blob/main/docs/systems/evidence/implementation-baseline.md)
- [Conceptual architecture](https://github.com/firestige/workflow-self-recursive/blob/main/docs/agent-architecture.md)
- [Observation Catalog](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/observation-catalog.md)
- [OTel Observation Profile](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/otel-observation-profile.md)
- [Execution–Evidence interaction contract](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/execution-evidence/interaction-contract.md)

## License

[Apache-2.0](LICENSE)
