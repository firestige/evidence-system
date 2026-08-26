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
