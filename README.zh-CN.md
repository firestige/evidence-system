# evidence-system

[English](README.md) | 中文

evidence-system 是 Agent Ops Ledger 的 Evidence System —— 一个可选、可独立部署的本地应用：它接收 Execution 发出的受支持 OTLP 事实，建立真实可信的因果与事实投影，并供人检查，但不控制执行。Evidence 或遥测不可用时，Execution 仍会继续。

三个 Module 分离关注点：

- **Observation Admission** 决定什么可以成为 accepted，解析 stable record identity，检测 duplicate/conflict，并协调事务。
- **Factual Projection** 推导 owner 范围内的因果与事实状态，并明确区分 final/lower-bound/unavailable/not-applicable 语义。
- **Query & Presentation** 以只读的 curated view 暴露已提交状态：Grafana 事实趋势与 Agent Decisions 因果 Trace 视图。

首个发行版由一个本地 Evidence App、一个 PostgreSQL 数据库与位于 App 之后的 Grafana 构成，仅暴露 loopback，数据库无对外可达监听。

## Developer preview

本仓库是 Agent Ops Ledger 架构优先开发者预览版的一部分，适用于个人或小团队的可信本地环境。当前发布 Evidence 设计与组件边界，尚未提供可供最终用户运行的发行版。**后续会有破坏兼容性的变更。**

## 获取源码

本仓库通常作为 [Agent Ops Ledger](https://github.com/firestige/workflow-self-recursive) 的 submodule 使用：

```sh
git clone --recurse-submodules https://github.com/firestige/workflow-self-recursive.git
```

单独克隆：

```sh
git clone https://github.com/firestige/evidence-system.git
```

## 文档

- [Evidence System 设计](https://github.com/firestige/workflow-self-recursive/blob/main/docs/systems/evidence/evidence-system.zh-CN.md)
- [概念架构](https://github.com/firestige/workflow-self-recursive/blob/main/docs/agent-architecture.zh-CN.md)
- [Observation Catalog](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/observation-catalog.zh-CN.md)
- [OTel Observation Profile](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/otel-observation-profile.zh-CN.md)
- [Execution–Evidence Interaction Contract](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/execution-evidence/interaction-contract.zh-CN.md)

## License

[Apache-2.0](LICENSE)
