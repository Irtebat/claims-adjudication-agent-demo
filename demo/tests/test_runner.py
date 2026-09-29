from pathlib import Path

import pytest

RUNNER = Path(__file__).parents[1] / "src" / "runner.py"


def test_notebook_import_cell_does_not_require_file():
    import_cell = RUNNER.read_text().split("# COMMAND ----------", maxsplit=2)[1]
    namespace = {}

    exec(compile(import_cell, str(RUNNER), "exec"), namespace)

    assert "__file__" not in namespace
    assert namespace["ClaimsGenerator"].__module__ == "generator_core"


def test_runner_has_no_file_dependency():
    assert "__file__" not in RUNNER.read_text()


def test_in_process_mode_fails_with_clear_bundle_guidance():
    adjudication_cell = RUNNER.read_text().split("# COMMAND ----------")[6]

    with pytest.raises(RuntimeError, match="not supported.*use mode=serving_endpoint"):
        exec(compile(adjudication_cell, str(RUNNER), "exec"), {"mode": "in_process", "count": 1})
