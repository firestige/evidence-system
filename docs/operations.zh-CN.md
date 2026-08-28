# Evidence 本地运维

受支持的本地拓扑由一个 Evidence data API 与一个内部 PostgreSQL database 组成。API 只发布在
`127.0.0.1:4318`；PostgreSQL 不发布 host port；部署不包含 Grafana、UI 或 presentation proxy。

## 启动与停止

在 repository root 执行：

```sh
./scripts/local-deployment.sh up
curl --fail http://127.0.0.1:4318/healthz
./scripts/local-deployment.sh down
```

第一次 `up` 会在已忽略的 `deployment/.secrets/` 下创建三个随机、mode-`0600` password file：

- `wsr_evidence_admin` 拥有 migration 与 restore 权限；
- `wsr_evidence_runtime` 只有 admission、query 与 retention 所需的 table/sequence 权限；
- `wsr_evidence_backup` 只有 `SELECT` 权限，且 transaction 默认 read-only。

不要把这些开发 credentials 复制到其他安装。在 managed environment 使用 Compose 时，通过
`WSR_EVIDENCE_ADMIN_PASSWORD_FILE`、`WSR_EVIDENCE_RUNTIME_PASSWORD_FILE` 与
`WSR_EVIDENCE_BACKUP_PASSWORD_FILE` 提供安装方持有的 secret file。

## 自动 retention

Evidence API 随 service process 启动 retention worker。Worker 在启动后立即运行一次，之后按配置的
interval 周期运行。Raw debug 保留独立 privacy scrub；queryable Evidence 使用另一个 terminal
Delivery identity bounded batch。某轮失败会被记录，但不会终止 API；后续 scheduled iteration 会重试。

Retention 仅由数据年龄与启动时 policy 驱动，不等待 ingest failure，也不检查 database size 或 free
disk space。Accepted terminal `delivery.summary` 超过 Delivery TTL 后，一个 transaction 物理删除该
Delivery 的 queryable Facts、Trace detail、Task membership/guard 与 Manifest。没有 trash/restore。
最小 query-invisible retirement fence 只防止迟到数据复活，不包含可恢复 dataset。

Automatic Delivery deletion 只适用于每个 accepted Event/Span 都直接携带 Delivery ID 的 Profile 2
dataset。Frozen Profile 1 record 不回填，也不通过 Trace/time inference 分配 owner。Task membership
row 是引用 authority：还有其他 Delivery 引用时保留 immutable Task declaration/display name，最后一条
membership 删除时一并回收。

| 环境变量 | 默认值 | 合法值 | 含义 |
|---|---:|---|---|
| `WSR_EVIDENCE_RAW_DEBUG_TTL` | `PT0S` | `PT0S`、`P0D` 或 `P1D` | Raw debug 立即或一天后可回收；禁止 `NEVER` |
| `WSR_EVIDENCE_DELIVERY_TTL` | `P30D` | `P1D`–`P3650D` 的整天数，或 `NEVER` | accepted terminal summary 后到 physical Delivery deletion 的年龄 |
| `WSR_EVIDENCE_RETENTION_BATCH_SIZE` | `500` | 整数 `1`–`1000` | 每轮最多处理的 Delivery 数 |
| `WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS` | `60` | 整秒 `10`–`3600` | 两轮之间的等待时间 |

Deleted Delivery 的 accepted record content 会被移除，只保留 non-queryable retirement fence。
设置 legacy Trace/factual/provenance TTL 变量会导致 startup error。Duration syntax
有意保持收敛：只接受 `PT0S`、整天 `P<n>D`，以及表中明确允许位置的 `NEVER`。全部 retention
setting 都在启动时读取，修改后需要重启 Evidence API。

仓库提供的 Compose file 直接映射 `WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS`。Managed Compose
安装若需配置 TTL 或 batch size，应通过安装方持有的 Compose override 把变量加入 `evidence`
service；只在 host export 一个未映射变量不会把它传入 container。例如：

```yaml
services:
  evidence:
    environment:
      WSR_EVIDENCE_DELIVERY_TTL: P90D
      WSR_EVIDENCE_RETENTION_BATCH_SIZE: "500"
```

MVP 不提供 disk-pressure cleanup、write-failure-triggered cleanup、manual delete API、logical deletion、
restore 或 automatic capacity tuning。运维方应独立监控 storage/backup，并只在上述已验证
范围内调整 bounded policy。

## 网络负向检查

以下命令是 deployment acceptance oracle 的一部分：

```sh
docker compose -f deployment/compose.yaml port evidence 4318
docker compose -f deployment/compose.yaml port database 5432
docker compose -f deployment/compose.yaml config --services
docker compose -f deployment/compose.yaml --profile operations config --services
```

第一条输出 `127.0.0.1` binding；第二条没有输出，因为 PostgreSQL 未发布到 host。默认 service list
只有 `database`、`migrate` 与 `evidence`；operations profile 只增加 `backup` 与 `restore`。两者都不
包含 Grafana 或 UI。

## 备份与恢复

通过 read-only role 创建 custom-format logical backup：

```sh
./scripts/local-deployment.sh backup evidence-20260826.backup
```

Backup 位于 Compose 管理的 `evidence-backups` volume。命令会校验 archive catalog 并输出 SHA-256。
请按本地 policy 将 archive 与 digest 复制到安装方持有的 protected storage。

Restore 不会覆盖 live database。它要求一个从未使用过、显式指定且以
`wsr_evidence_restore_` 开头的 target：

```sh
./scripts/local-deployment.sh restore evidence-20260826.backup wsr_evidence_restore_20260826
```

校验完成后，promote 或删除 restored database 都必须由 administrator 显式执行。Restore 会拒绝
已存在 target，避免意外覆盖。`make deployment` 会在临时 Compose project 中验证 role limits、
backup、restore 与 state comparison，并在结束后移除测试 volume。
