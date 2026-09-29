from pathlib import Path


def test_notebook_import_cell_does_not_require_file():
    runner = Path(__file__).parents[1] / "src" / "runner.py"
    import_cell = runner.read_text().split("# COMMAND ----------", maxsplit=2)[1]
    namespace = {}

    exec(compile(import_cell, str(runner), "exec"), namespace)

    assert "__file__" not in namespace
    assert namespace["ClaimsGenerator"].__module__ == "generator_core"
