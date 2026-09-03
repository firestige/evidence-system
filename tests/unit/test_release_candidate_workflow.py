from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_candidate_asset_verification_uses_the_built_package_version() -> None:
    workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text()

    assert 'VERSION="$(uv run python -m release.cli.release verify' in workflow
    assert '"wsr_evidence-${VERSION}-py3-none-any.whl"' in workflow
    assert '"wsr_evidence-${VERSION}.tar.gz"' in workflow
    assert "wsr_evidence-0.1.0" not in workflow
