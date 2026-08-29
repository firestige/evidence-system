import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from release.validate_image_qualification import (  # noqa: E402
    QualificationError,
    validate_remote,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "release"
PRODUCT = "a" * 40


def fixture(name: str) -> dict[str, object]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_platform_qualification_accepts_both_exact_attestations() -> None:
    validate_remote(
        provenance=fixture("platform-provenance.json"),
        image_config=fixture("platform-image.json"),
        product_commit=PRODUCT,
    )


@pytest.mark.parametrize(
    ("document", "mutation"),
    [
        ("provenance", "missing-platform"),
        ("provenance", "wrong-revision"),
        ("provenance", "wrong-product"),
        ("provenance", "wrong-source"),
        ("image", "missing-platform"),
        ("image", "wrong-product"),
        ("image", "wrong-source"),
    ],
)
def test_platform_qualification_rejects_missing_or_mismatched_attestations(
    document: str, mutation: str
) -> None:
    provenance = fixture("platform-provenance.json")
    image = fixture("platform-image.json")
    target = provenance if document == "provenance" else image
    if mutation == "missing-platform":
        target.pop("linux/arm64")
    elif document == "provenance":
        args = target["linux/arm64"]["SLSA"]["buildDefinition"]["externalParameters"][  # type: ignore[index]
            "request"
        ]["root"]["request"]["args"]
        key = {
            "wrong-revision": "vcs:revision",
            "wrong-product": "build-arg:WSR_RELEASE_REVISION",
            "wrong-source": "vcs:source",
        }[mutation]
        args[key] = "mismatch"
    else:
        labels = target["linux/arm64"]["config"]["Labels"]  # type: ignore[index]
        key = {
            "wrong-product": "org.opencontainers.image.revision",
            "wrong-source": "org.opencontainers.image.source",
        }[mutation]
        labels[key] = "mismatch"

    with pytest.raises(QualificationError):
        validate_remote(
            provenance=copy.deepcopy(provenance),
            image_config=copy.deepcopy(image),
            product_commit=PRODUCT,
        )
