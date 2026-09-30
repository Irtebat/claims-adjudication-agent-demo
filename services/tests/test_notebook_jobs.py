"""Guards for the job files that run as Databricks ``notebook_task`` sources.

A notebook task has no ``__file__``, never sets ``__name__ == "__main__"``, and passes
``base_parameters`` through ``dbutils.widgets`` rather than ``sys.argv``. Sibling modules
import directly because the notebook's directory is the working directory.
"""

import ast
import re
import sys
import types
from pathlib import Path

import pytest

SERVICES = Path(__file__).parents[1]
SRC = SERVICES / "src"
RESOURCES = SERVICES / "resources"

# job file -> the module-level statement that ends the run.
JOBS = {
    "producer": "query.awaitTermination",
    "worker": "dbutils.notebook.exit",
    "relay": "dbutils.notebook.exit",
    "consumers": "dbutils.notebook.exit",
    "migrate": "dbutils.notebook.exit",
}


def _tree(job):
    return ast.parse((SRC / f"{job}.py").read_text())


def _base_parameter_keys(job):
    """Collect base_parameters keys for every task whose notebook_path is this job."""
    keys, in_params, params_indent, is_job = set(), False, 0, False
    for line in (RESOURCES / f"{job}.yml").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("notebook_path:"):
            is_job = stripped.endswith(f"/src/{job}.py")
        if in_params:
            if indent > params_indent:
                keys.add(stripped.split(":", 1)[0])
                continue
            in_params = False
        if stripped == "base_parameters:" and is_job:
            in_params, params_indent = True, indent
    return keys


def _widget_names(tree):
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) == "dbutils.widgets"
            and node.func.attr in {"text", "dropdown"}
        ):
            names.add(node.args[0].value)
    return names


def test_no_file_references_in_src():
    offenders = [p.name for p in SRC.glob("*.py") if "__file__" in p.read_text()]
    assert offenders == []


@pytest.mark.parametrize("job", JOBS)
def test_job_is_notebook_source(job):
    assert (SRC / f"{job}.py").read_text().startswith("# Databricks notebook source\n")


@pytest.mark.parametrize("job", JOBS)
def test_import_prologue_runs_without_file(job, monkeypatch):
    """Exec the imports as a notebook would: no __file__, no sys.path bootstrap."""
    # Runtime-only third-party deps; any attribute import resolves to the stub itself.
    for name in (
        "psycopg",
        "databricks",
        "databricks.sdk",
        "pyspark",
        "pyspark.sql",
        "pyspark.sql.types",
    ):
        stub = types.ModuleType(name)
        stub.__getattr__ = lambda attr, stub=stub: stub
        monkeypatch.setitem(sys.modules, name, stub)
    tree = _tree(job)
    body = []
    for node in tree.body:
        if re.search(r"\b(dbutils|spark)\b", ast.unparse(node)):
            break
        body.append(node)
    prologue = ast.Module(body=body, type_ignores=[])
    namespace = {"__name__": "__main__"}
    exec(compile(prologue, str(SRC / f"{job}.py"), "exec"), namespace)
    assert "__file__" not in namespace


@pytest.mark.parametrize("job", JOBS)
def test_main_logic_runs_at_module_level(job):
    tree = _tree(job)
    source = ast.unparse(tree)
    assert "__main__" not in source
    assert not re.search(r"\bargparse\b|\bsys\.argv\b", source)
    # Parameters are read and the run is finished by top-level statements, not by a
    # function the notebook would never call.
    top_level = [ast.unparse(n) for n in tree.body]
    assert any("dbutils.widgets.get(" in s for s in top_level)
    assert any(s.startswith(JOBS[job] + "(") for s in top_level)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert "dbutils.widgets" not in ast.unparse(node), f"{job}.{node.name}"


@pytest.mark.parametrize("job", JOBS)
def test_widgets_match_base_parameters(job):
    keys = _base_parameter_keys(job)
    assert keys, f"no base_parameters found for {job}"
    assert keys == _widget_names(_tree(job))


def test_no_cache_or_persist_on_serverless():
    offenders = [
        p.name for p in SRC.glob("*.py") if re.search(r"\.(cache|persist)\(", p.read_text())
    ]
    assert offenders == []
