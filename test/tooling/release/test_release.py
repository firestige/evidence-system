from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[3]
SPEC = importlib.util.spec_from_file_location(
    "wsr_evidence_release", ROOT / "release/cli/release.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
assert isinstance(release, ModuleType)
ReleaseError = release.ReleaseError
assert_configuration = release.assert_configuration
simulate_lifecycle = release.simulate_lifecycle
verify_manifest = release.verify_manifest


def test_python_adapter_configuration_does_not_select_npm() -> None:
    config = json.loads((ROOT / "release/config/component.json").read_text())

    assert_configuration(config)

    assert config["repository"] == "firestige/evidence-system"
    assert config["assetMode"] == "python-wheel-sdist+oci"
    assert config["publisherAdapter"] == "python-github-release+ghcr"
    assert "npm" not in json.dumps(config).lower()


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("happy", "STABLE"),
        ("candidate-main-divergence", "STABLE"),
        ("npm-partial-failure", "UNSUPPORTED_SCENARIO"),
    ],
)
def test_language_neutral_lifecycle_with_python_capability_boundary(
    scenario: str, expected: str
) -> None:
    assert simulate_lifecycle(scenario) == expected


@pytest.mark.parametrize(
    "scenario",
    ["digest-mismatch", "tag-collision", "permission-denied", "builtin-token-final-publish"],
)
def test_release_failures_stop_before_stable(scenario: str) -> None:
    with pytest.raises(ReleaseError):
        simulate_lifecycle(scenario)


def test_manifest_verifier_requires_wheel_sdist_and_exact_oci_digest(tmp_path: Path) -> None:
    wheel = tmp_path / "wsr_evidence-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "wsr_evidence-0.1.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    manifest = {
        "schemaVersion": "wsr.evidence-release@1.0.0",
        "version": "0.1.0",
        "ociDigest": "sha256:" + "a" * 64,
        "artifacts": [],
    }
    for file in (wheel, sdist):
        import hashlib

        manifest["artifacts"].append(
            {
                "name": file.name,
                "bytes": file.stat().st_size,
                "sha256": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        )
    (tmp_path / "release-metadata.json").write_text(json.dumps(manifest))

    assert verify_manifest(tmp_path) == "0.1.0"
    wheel.write_bytes(b"changed")
    with pytest.raises(ReleaseError, match="RELEASE_ARTIFACT_DIGEST_MISMATCH"):
        verify_manifest(tmp_path)


def test_workflows_keep_app_token_at_the_final_github_publish_boundary() -> None:
    candidate = (ROOT / ".github/workflows/release-candidate.yml").read_text()
    promote = (ROOT / ".github/workflows/release-promote.yml").read_text()
    dockerfile = (ROOT / "deployment/Dockerfile").read_text()

    assert "WSR_RELEASE_APP_PRIVATE_KEY" not in candidate
    assert "push:" in candidate
    assert "release/request.json" in candidate
    assert "steps.request.outputs.candidate_tag" in candidate
    assert "docker buildx imagetools inspect" in candidate
    assert "org.opencontainers.image.revision" in candidate
    assert "ARG WSR_RELEASE_REVISION" in dockerfile
    assert "org.opencontainers.image.revision=$WSR_RELEASE_REVISION" in dockerfile
    assert "workflow_call:" in candidate
    assert 'test "$GITHUB_REF_NAME" = "release/next"' in candidate
    assert "actions/create-github-app-token@" in promote
    assert promote.index("actions/create-github-app-token@") > promote.index(
        "Verify qualified assets"
    )
    assert "GH_TOKEN: ${{ steps.release-app-token.outputs.token }}" in promote
    assert "repositories: evidence-system" in promote
    assert "permission-contents: write" in promote
    assert "ghcr.io/firestige/wsr-evidence@$OCI_DIGEST" in promote
