import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "wsr_evidence"
LAYER_DEPENDENCIES = {
    "admission": {"clock", "errors", "model", "projection", "storage"},
    "projection": {"clock", "errors", "model"},
    "query": {"clock", "errors", "model", "storage"},
    "retention": {"clock", "errors", "model", "storage"},
    "storage": {"clock", "errors", "model"},
    "transport": {"admission", "config", "errors", "model", "query"},
}


def test_owned_layers_exist_and_only_import_allowed_dependencies() -> None:
    for layer, allowed in LAYER_DEPENDENCIES.items():
        layer_path = PACKAGE_ROOT / layer
        assert layer_path.is_dir(), f"missing owned layer: {layer}"

        for source_path in layer_path.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imported_layers = {
                node.module.split(".")[1]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("wsr_evidence.")
                and len(node.module.split(".")) > 1
            }
            disallowed = imported_layers - allowed - {layer}
            assert not disallowed, f"{source_path}: disallowed imports {sorted(disallowed)}"
