# evidence-system

English | [中文](README.zh-CN.md)

evidence-system is the Evidence System of workflow-self-recursive — an optional, separately deployable local application that accepts supported OTLP facts from Execution, projects truthful causal and factual views, and serves human inspection without controlling execution. Execution continues when Evidence or telemetry is unavailable.

Three modules separate the concerns:

- **Observation Admission** decides what may become accepted, resolves stable record identity, detects duplicate/conflict, and coordinates the transaction.
- **Factual Projection** derives owner-scoped causal and factual state with explicit final/lower-bound/unavailable/not-applicable semantics.
- **Query & Presentation** exposes committed state as read-only curated views: Grafana factual trends and an Agent Decisions causal Trace view.

The first release runs as one local Evidence App, one PostgreSQL database, and Grafana behind the App, with loopback-only exposure and no externally reachable database listener.

## Developer preview

This repository is part of workflow-self-recursive's architecture-first developer preview for trusted local use by individuals and small teams. It publishes the Evidence design and component boundaries; it does not yet provide a runnable end-user release. **THERE WILL BE COMPATIBILITY-BREAKING CHANGES.**

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
- [Conceptual architecture](https://github.com/firestige/workflow-self-recursive/blob/main/docs/agent-architecture.md)
- [Observation Catalog](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/observation-catalog.md)
- [OTel Observation Profile](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/otel-observation-profile.md)
- [Execution–Evidence interaction contract](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/execution-evidence/interaction-contract.md)

## License

[Apache-2.0](LICENSE)
