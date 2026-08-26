from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from wsr_evidence.admission.service import AdmissionService
from wsr_evidence.admission.validation import ValidationError, validate_record

FIXTURE = Path(__file__).parents[1] / "fixtures" / "wave3_admission_projection.json"


def test_wave3_admission_and_read_model_golden_fixture() -> None:
    document: dict[str, Any] = json.loads(FIXTURE.read_text())
    assert document["fixture_version"] == "1.0.0"

    for case in document["cases"]:
        if case["decision"] == "REJECT":
            with pytest.raises(ValidationError):
                validate_record(case["record"])
            continue

        validated = validate_record(case["record"])
        effects = AdmissionService.project(validated)
        assert list(validated.identity) == case["identity"], case["case_id"]
        assert [effect.kind for effect in effects] == case["effect_kinds"], case["case_id"]
        if "compatibility_key" in case:
            assert list(effects[0].payload["compatibility_key"]) == case["compatibility_key"]
            assert effects[0].payload["aggregate_eligible"] is case["aggregate_eligible"]
