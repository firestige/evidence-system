# Evidence release adapter

The repository-local adapter implements the shared `wsr.release-component@1.0.0` lifecycle without selecting npm. It publishes wheel/sdist assets and a digest-bound GHCR image.

Validate locally with one command:

```sh
uv run python -m release.cli.release config
```

For an RC, first record the immutable Evidence product commit in the superproject unified candidate manifest. The selected superproject authority must also contain the FROZEN `evidence.query@0.1.0` publication record. Then advance the publisher tooling to `release/next` and dispatch **Evidence CI** on that branch with `release_candidate=true`, the RC tag, the exact superproject authority commit, and `release/candidates/iter4-wave11.json`.

The candidate workflow keeps three identities separate:

- the superproject authority supplies the FROZEN Contract and unified manifest;
- the manifest's `evidence.candidate_archive_commit` supplies the wheel, sdist, OCI content, and RC tag target;
- the `release/next` commit supplies publisher tooling only.

Qualification uses the repository token for read-only GitHub operations and the candidate GHCR push. It mints a repository-scoped GitHub App token only after all local gates and exact artifact checks pass, immediately before creating or resuming the RC. Stable promotion is a separate dispatch after A3 approval and component-first superproject repin; it reuses the qualified OCI digest and also mints the App token only at the final GitHub Release boundary.
