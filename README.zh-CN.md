# evidence-system

[English](README.md) | 中文

evidence-system 是 workflow-self-recursive 的 Evidence System —— 一个可选、可独立部署且仅限 loopback 的数据服务。它接收 Execution 发出的受支持 OTLP 事实，持久化真实可信的因果与事实投影，并通过带版本的只读查询 API 暴露已提交状态，但不控制执行。Evidence 或遥测不可用时，Execution 仍会继续。

三个 Module 分离关注点：

- **Observation Admission** 决定什么可以成为 accepted，解析 stable record identity，检测 duplicate/conflict，并协调事务。
- **Factual Projection** 推导 owner 范围内的因果与事实状态，并明确区分 final/lower-bound/unavailable/not-applicable 语义。
- **Query & API** 是 BI 与 Evolution 读取已提交状态的唯一外部边界。

首个发行版由一个本地 Evidence API 服务和一个内部 PostgreSQL 数据库组成。它不托管 UI、不代理展示层，也不向消费者暴露 PostgreSQL。

## Developer preview

本仓库是 workflow-self-recursive 架构优先开发者预览版的一部分，适用于个人或小团队的可信本地环境。Admission、projection、query、automatic retention 与本地 deployment 均已实现且可测试。**后续会有破坏兼容性的变更。**

## 开发

支持 Python 3.13 与 3.14；本地开发默认选择可用的最新 3.14 patch。依赖锁定和构建由 [uv](https://docs.astral.sh/uv/) 管理。

```sh
make sync         # 按 uv.lock 创建精确依赖环境
make format       # 重写 Python 格式
make lint         # 格式检查、Ruff lint 与严格 mypy
make unit         # 无外部服务的小型测试
make integration  # 临时 PostgreSQL 18 migration 与集成测试
make deployment   # 构建并 smoke-test loopback Docker Compose 部署
make build        # 在 dist/ 生成 wheel 与 sdist
make check        # 非容器质量/构建门
```

受支持的 Compose 部署只在 `127.0.0.1:4318` 发布 API；PostgreSQL 不向宿主机发布端口。运行时启动不会隐式执行 migration。

本地启动、automatic retention、数据库角色分离、文件型 secret、只读备份、拒绝覆盖的恢复流程与精确网络负向检查见 [Evidence 本地运维](docs/operations.zh-CN.md)。

## 获取源码

本仓库通常作为 [workflow-self-recursive](https://github.com/firestige/workflow-self-recursive) 的 submodule 使用：

```sh
git clone --recurse-submodules https://github.com/firestige/workflow-self-recursive.git
```

单独克隆：

```sh
git clone https://github.com/firestige/evidence-system.git
```

## 文档

- [Evidence System 设计](https://github.com/firestige/workflow-self-recursive/blob/main/docs/systems/evidence/evidence-system.zh-CN.md)
- [Evidence 实现基线](https://github.com/firestige/workflow-self-recursive/blob/main/docs/systems/evidence/implementation-baseline.zh-CN.md)
- [Evidence 本地运维与 retention](docs/operations.zh-CN.md)
- [概念架构](https://github.com/firestige/workflow-self-recursive/blob/main/docs/agent-architecture.zh-CN.md)
- [Observation Catalog](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/observation-catalog.zh-CN.md)
- [OTel Observation Profile](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/observation/otel-observation-profile.zh-CN.md)
- [Execution–Evidence Interaction Contract](https://github.com/firestige/workflow-self-recursive/blob/main/docs/contracts/execution-evidence/interaction-contract.zh-CN.md)

## License

[Apache-2.0](LICENSE)
