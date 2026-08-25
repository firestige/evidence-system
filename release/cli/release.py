"""No-dependency Evidence release adapter and fail-closed lifecycle oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

CONFIG_KEYS = {
    "schemaVersion",
    "repository",
    "releaseBranch",
    "triggerBranch",
    "assetMode",
    "acceptanceCommand",
    "buildCommand",
    "verifyCommand",
    "publisherAdapter",
    "remoteInstallMode",
    "stablePolicy",
    "capabilities",
}
SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")


class ReleaseError(RuntimeError):
    pass


def assert_configuration(value: dict[str, Any]) -> None:
    if (
        set(value) != CONFIG_KEYS
        or value.get("schemaVersion") != "wsr.release-component@1.0.0"
        or value.get("releaseBranch") != "main"
        or not re.fullmatch(r"release/[a-z0-9._-]+", value.get("triggerBranch", ""))
        or value.get("stablePolicy") != "qualified-candidate-exact-assets"
        or not isinstance(value.get("capabilities"), list)
        or not value["capabilities"]
        or len(set(value["capabilities"])) != len(value["capabilities"])
    ):
        raise ReleaseError("RELEASE_CONFIGURATION_INVALID")


def simulate_lifecycle(scenario: str) -> str:
    if scenario in {"happy", "candidate-main-divergence"}:
        return "STABLE"
    if scenario == "npm-partial-failure":
        return "UNSUPPORTED_SCENARIO"
    failures = {
        "digest-mismatch": "RELEASE_ARTIFACT_DIGEST_MISMATCH",
        "tag-collision": "RELEASE_TAG_COLLISION",
        "permission-denied": "RELEASE_PERMISSION_DENIED",
        "builtin-token-final-publish": "RELEASE_APP_TOKEN_REQUIRED",
    }
    if scenario in failures:
        raise ReleaseError(failures[scenario])
    raise ReleaseError("RELEASE_SCENARIO_UNKNOWN")


def _digest(file: Path) -> str:
    return "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()


def verify_manifest(directory: Path) -> str:
    try:
        manifest = json.loads((directory / "release-metadata.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError("RELEASE_METADATA_INVALID") from error
    if (
        set(manifest) != {"schemaVersion", "version", "ociDigest", "artifacts"}
        or manifest["schemaVersion"] != "wsr.evidence-release@1.0.0"
        or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", manifest["version"])
        or SHA256.fullmatch(manifest["ociDigest"]) is None
        or not isinstance(manifest["artifacts"], list)
    ):
        raise ReleaseError("RELEASE_METADATA_INVALID")
    expected_names = {
        f"wsr_evidence-{manifest['version']}-py3-none-any.whl",
        f"wsr_evidence-{manifest['version']}.tar.gz",
    }
    if {item.get("name") for item in manifest["artifacts"]} != expected_names:
        raise ReleaseError("RELEASE_ARTIFACT_SET_INVALID")
    for item in manifest["artifacts"]:
        file = directory / item["name"]
        if (
            not file.is_file()
            or item.get("bytes") != file.stat().st_size
            or item.get("sha256") != _digest(file)
        ):
            raise ReleaseError("RELEASE_ARTIFACT_DIGEST_MISMATCH")
    return str(manifest["version"])


def build_manifest(directory: Path, oci_digest: str) -> str:
    if SHA256.fullmatch(oci_digest) is None:
        raise ReleaseError("RELEASE_OCI_DIGEST_INVALID")
    repository = Path(__file__).parents[2]
    version = str(tomllib.loads((repository / "pyproject.toml").read_text())["project"]["version"])
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(["uv", "build", "--out-dir", str(directory)], cwd=repository, check=True)
    artifacts = []
    for file in sorted(directory.iterdir()):
        if file.suffix == ".whl" or file.name.endswith(".tar.gz"):
            artifacts.append(
                {"name": file.name, "bytes": file.stat().st_size, "sha256": _digest(file)}
            )
    manifest = {
        "schemaVersion": "wsr.evidence-release@1.0.0",
        "version": version,
        "ociDigest": oci_digest,
        "artifacts": artifacts,
    }
    (directory / "release-metadata.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return verify_manifest(directory)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config")
    simulate = subparsers.add_parser("simulate")
    simulate.add_argument("scenario")
    build = subparsers.add_parser("build")
    build.add_argument("directory", type=Path)
    build.add_argument("oci_digest")
    verify = subparsers.add_parser("verify")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "config":
        config = json.loads((Path(__file__).parents[1] / "config/component.json").read_text())
        assert_configuration(config)
        result: Any = {"repository": config["repository"], "status": "PASS"}
    elif args.command == "simulate":
        result = {"scenario": args.scenario, "state": simulate_lifecycle(args.scenario)}
    elif args.command == "build":
        result = {"version": build_manifest(args.directory, args.oci_digest), "status": "PASS"}
    else:
        result = {"version": verify_manifest(args.directory), "status": "PASS"}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
