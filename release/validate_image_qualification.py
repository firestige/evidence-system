#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PLATFORMS = {"linux/amd64", "linux/arm64"}
BUILD_TYPE = "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
SOURCE = "https://github.com/firestige/evidence-system"


class QualificationError(RuntimeError):
    pass


def validate_provenance(value: dict[str, Any], *, product_commit: str) -> None:
    try:
        if set(value) != PLATFORMS:
            raise QualificationError("EVIDENCE_IMAGE_PROVENANCE_INVALID")
        for platform in sorted(PLATFORMS):
            build = value[platform]["SLSA"]["buildDefinition"]
            arguments = build["externalParameters"]["request"]["root"]["request"]["args"]
            if (
                build["buildType"] != BUILD_TYPE
                or arguments["vcs:source"] != SOURCE
                or arguments["vcs:revision"] != product_commit
                or arguments["build-arg:WSR_RELEASE_REVISION"] != product_commit
            ):
                raise QualificationError("EVIDENCE_IMAGE_PROVENANCE_INVALID")
    except (KeyError, TypeError) as error:
        raise QualificationError("EVIDENCE_IMAGE_PROVENANCE_INVALID") from error


def validate_image_config(value: dict[str, Any], *, product_commit: str) -> None:
    try:
        if set(value) != PLATFORMS:
            raise QualificationError("EVIDENCE_IMAGE_CONFIG_INVALID")
        for platform in sorted(PLATFORMS):
            labels = value[platform]["config"]["Labels"]
            if (
                labels["org.opencontainers.image.source"] != SOURCE
                or labels["org.opencontainers.image.revision"] != product_commit
            ):
                raise QualificationError("EVIDENCE_IMAGE_CONFIG_INVALID")
    except (KeyError, TypeError) as error:
        raise QualificationError("EVIDENCE_IMAGE_CONFIG_INVALID") from error


def validate_remote(
    *, provenance: dict[str, Any], image_config: dict[str, Any], product_commit: str
) -> None:
    validate_provenance(provenance, product_commit=product_commit)
    validate_image_config(image_config, product_commit=product_commit)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("EVIDENCE_IMAGE_QUALIFICATION_INPUT_INVALID") from error
    if not isinstance(value, dict):
        raise QualificationError("EVIDENCE_IMAGE_QUALIFICATION_INPUT_INVALID")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--image-config", type=Path, required=True)
    parser.add_argument("--product-commit", required=True)
    args = parser.parse_args()
    try:
        validate_remote(
            provenance=load(args.provenance),
            image_config=load(args.image_config),
            product_commit=args.product_commit,
        )
    except QualificationError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"productCommit": args.product_commit, "status": "PASS"}, sort_keys=True))


if __name__ == "__main__":
    main()
