# Evidence release adapter

The repository-local adapter implements the shared `wsr.release-component@1.0.0` lifecycle without selecting npm. It publishes wheel/sdist assets and a digest-bound GHCR image.

Validate locally with one command:

```sh
uv run python -m release.cli.release config
```

For a release, merge the exact candidate to `release/next`, select that ref in GitHub Actions, and dispatch **Evidence release candidate**. Stable promotion is a separate dispatch after qualification and component-first superproject repin. Only its final GitHub Release step receives a short-lived GitHub App installation token.
