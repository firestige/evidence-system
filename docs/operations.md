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
