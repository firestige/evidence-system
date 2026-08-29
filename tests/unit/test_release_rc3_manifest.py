import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
PRODUCT_COMMIT = "35d63469650e978d0fb795419df5d4a0ea5eafa7"


def test_rc3_manifest_binds_task_route_product_schema_and_assets() -> None:
    candidate = json.loads(
        (ROOT / "release" / "candidates" / "iter6-wave10-rc3.json").read_text(encoding="utf-8")
    )
    evidence = candidate["evidence"]

    assert candidate["schema_version"] == "wsr.evidence-immutable-candidate@1.0.0"
    assert candidate["status"] == "IMMUTABLE_RELEASE_CANDIDATE"
    assert candidate["candidate_tag"] == "0.1.0-rc.3"
    assert evidence["candidate_archive_commit"] == PRODUCT_COMMIT
    assert evidence["payload_commit"] == "7e3ff4a9f87b17b428a07054ff9826aeb863b57f"
    assert evidence["migration_revision"] == "20260828_0004"
    assert "/v1/evidence/tasks" in (ROOT / "src/wsr_evidence/transport/query.py").read_text()
    assert candidate["gates"]["task_route_and_schema"] == "PASS"
    assert candidate["assets"] == [
        {
            "name": "wsr_evidence-0.1.0-py3-none-any.whl",
            "bytes": 55732,
            "sha256": "sha256:1cc5587fea69ba5efb4b77e3824878b3ce2caa9780c1a9c1ff8e37fe01f2408f",
        },
        {
            "name": "wsr_evidence-0.1.0.tar.gz",
            "bytes": 43873,
            "sha256": "sha256:b2e1458dd511403d002b303e57de155bd4cd84dcbba9467db020c04aa29490e8",
        },
    ]
