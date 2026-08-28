# Evidence local operations

The supported local topology is one Evidence data API plus an internal PostgreSQL database. The API is published only at `127.0.0.1:4318`. PostgreSQL has no host port, and this deployment contains no Grafana, UI, or presentation proxy.

## Start and stop

From the repository root:

```sh
./scripts/local-deployment.sh up
curl --fail http://127.0.0.1:4318/healthz
./scripts/local-deployment.sh down
```

The first `up` creates three random, mode-`0600` password files under ignored `deployment/.secrets/`:

- `wsr_evidence_admin` owns migrations and restore operations;
- `wsr_evidence_runtime` has only the table/sequence privileges needed by admission, query, and retention;
- `wsr_evidence_backup` has `SELECT` only and defaults to read-only transactions.

Do not copy these development credentials into another installation. Supply installation-owned secret files through `WSR_EVIDENCE_ADMIN_PASSWORD_FILE`, `WSR_EVIDENCE_RUNTIME_PASSWORD_FILE`, and `WSR_EVIDENCE_BACKUP_PASSWORD_FILE` when running Compose in a managed environment.

## Automatic retention

The Evidence API starts its retention worker with the service process. The worker runs once immediately
after startup and then once per configured interval. Each iteration processes at most one bounded batch
for each enabled resource class, in this order: Raw debug, Trace detail, and factual projection. A failed
iteration is logged without terminating the API; a later scheduled iteration tries again.

Retention is driven only by age and the startup policy. It does not wait for an ingest failure or check
database size or free disk space. Expiry scrubs eligible payloads and retains the published identity,
provenance, tombstone, and availability/expiry state; it is not a manual Delivery-deletion facility.

| Environment variable | Default | Accepted value | Meaning |
|---|---:|---|---|
| `WSR_EVIDENCE_RAW_DEBUG_TTL` | `PT0S` | `PT0S`, `P0D`, or `P1D` | Raw debug becomes eligible immediately or after one day; `NEVER` is forbidden |
| `WSR_EVIDENCE_TRACE_DETAIL_TTL` | `P30D` | whole days from `P1D` through `P365D`, or `NEVER` | Retention age for Trace detail |
| `WSR_EVIDENCE_FACTUAL_PROJECTION_TTL` | `P365D` | whole days from `P30D` through `P3650D`, or `NEVER` | Retention age for factual projections |
| `WSR_EVIDENCE_RETENTION_BATCH_SIZE` | `500` | integer `1`–`1000` | Maximum resources processed per enabled class in one iteration |
| `WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS` | `60` | whole seconds `10`–`3600` | Delay between iterations |

Accepted identity and provenance never expire. Setting
`WSR_EVIDENCE_ACCEPTED_PROVENANCE_TTL` is therefore a startup error, rather than an override. Duration
syntax is intentionally narrow: only `PT0S`, whole-day `P<n>D`, and `NEVER` where listed are accepted.
All retention settings are read at startup, so changing one requires restarting the Evidence API.

The supplied Compose file maps `WSR_EVIDENCE_RETENTION_INTERVAL_SECONDS` directly. To configure TTLs or
the batch size in a managed Compose installation, add them to the `evidence` service through an
installation-owned Compose override; merely exporting an unmapped host variable does not pass it into
the container. For example:

```yaml
services:
  evidence:
    environment:
      WSR_EVIDENCE_TRACE_DETAIL_TTL: P90D
      WSR_EVIDENCE_FACTUAL_PROJECTION_TTL: P730D
      WSR_EVIDENCE_RETENTION_BATCH_SIZE: "500"
```

The MVP provides no disk-pressure cleanup, write-failure-triggered cleanup, manual delete API,
Delivery-atomic garbage collection, or automatic capacity tuning. Operators should monitor storage and
backup independently and adjust the bounded policy only within the validated ranges above.

## Network negatives

These commands are part of the deployment acceptance oracle:

```sh
docker compose -f deployment/compose.yaml port evidence 4318
docker compose -f deployment/compose.yaml port database 5432
docker compose -f deployment/compose.yaml config --services
docker compose -f deployment/compose.yaml --profile operations config --services
```

The first prints a `127.0.0.1` binding. The second prints nothing because PostgreSQL is not published to the host. The default service list contains only `database`, `migrate`, and `evidence`; the operations profile adds only `backup` and `restore`. Neither list contains Grafana or UI.

## Backup and restore

Create a custom-format logical backup through the read-only role:

```sh
./scripts/local-deployment.sh backup evidence-20260826.backup
```

Backups live in the Compose-managed `evidence-backups` volume. The command verifies the archive catalog and prints its SHA-256. Copy the archive and its digest to installation-owned protected storage according to local policy.

Restore never overwrites the live database. It requires a previously unused, explicit target whose name starts with `wsr_evidence_restore_`:

```sh
./scripts/local-deployment.sh restore evidence-20260826.backup wsr_evidence_restore_20260826
```

After verification, promotion or deletion of a restored database is an explicit administrator action. The restore operation refuses an existing target, preventing accidental overwrite. `make deployment` exercises role limits, backup, restore, and state comparison against an ephemeral Compose project and removes its test volumes afterward.
