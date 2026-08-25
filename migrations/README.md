# Migration ownership

Only the storage owner may add or reorder core migrations. Query and retention waves may add
migrations only in their allocated namespaces and may not rewrite an existing revision. Runtime
startup never runs migrations implicitly; deployment invokes `alembic upgrade head` as a separate,
controlled write-capable role.
