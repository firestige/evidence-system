from pathlib import Path

ROOT = Path(__file__).parents[2]
COMPOSE = (ROOT / "deployment" / "compose.yaml").read_text(encoding="utf-8")


def test_supported_deployment_has_closed_network_and_service_surface() -> None:
    assert '"127.0.0.1:4318:4318"' in COMPOSE
    database_block = COMPOSE.split("  database:\n", 1)[1].split("\n  migrate:", 1)[0]
    assert "ports:" not in database_block
    assert "POSTGRES_HOST_AUTH_METHOD" not in COMPOSE
    assert "grafana" not in COMPOSE.lower()
    assert "ui:" not in COMPOSE.lower()


def test_database_roles_and_credentials_are_separated() -> None:
    for role in ["wsr_evidence_admin", "wsr_evidence_runtime", "wsr_evidence_backup"]:
        assert role in COMPOSE
    for secret in ["admin_password", "runtime_password", "backup_password"]:
        assert secret in COMPOSE
    assert (ROOT / "deployment" / "postgres" / "init-roles.sh").is_file()
    assert (ROOT / "deployment" / "run-with-database-secret.sh").is_file()


def test_backup_and_restore_are_explicit_operations() -> None:
    assert 'profiles: ["operations"]' in COMPOSE
    assert "  backup:" in COMPOSE
    assert "  restore:" in COMPOSE
    assert (ROOT / "deployment" / "postgres" / "backup.sh").is_file()
    assert (ROOT / "deployment" / "postgres" / "restore.sh").is_file()
    assert (ROOT / "docs" / "operations.md").is_file()


def test_smoke_secrets_are_container_readable_behind_a_private_host_directory() -> None:
    smoke = (ROOT / "scripts" / "deployment-smoke.sh").read_text(encoding="utf-8")

    assert 'chmod 700 "$smoke_dir"' in smoke
    assert 'chmod 644 "$smoke_dir"/*-password' in smoke
    assert 'chmod 600 "$smoke_dir"/*-password' not in smoke


def test_release_image_is_multi_platform_and_emits_build_provenance() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(
        encoding="utf-8"
    )

    assert "--platform linux/amd64,linux/arm64" in workflow
    assert "--provenance=mode=max" in workflow
    assert "--sbom=true" in workflow
    assert "docker buildx imagetools inspect" in workflow
    assert 'index("amd64") != null and index("arm64") != null' in workflow
