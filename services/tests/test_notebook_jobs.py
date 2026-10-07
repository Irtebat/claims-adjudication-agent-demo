"""Guards for the job files that run as Databricks ``notebook_task`` sources.

A notebook task has no ``__file__``, never sets ``__name__ == "__main__"``, and passes
``base_parameters`` through ``dbutils.widgets`` rather than ``sys.argv``. Sibling modules
import directly because the notebook's directory is the working directory.
"""

import ast
import os
import re
import subprocess
import sys
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


_STUB = """\
class _Any:
    def __getattr__(self, name):
        return self

    def __call__(self, *args, **kwargs):
        return self


def __getattr__(name):
    return _Any()
"""

# Runtime-only third-party deps, stubbed as files so the subprocess imports them normally.
_STUB_FILES = (
    "psycopg.py",
    "confluent_kafka.py",
    "databricks/__init__.py",
    "databricks/sdk.py",
    "pyspark/__init__.py",
    "pyspark/sql/__init__.py",
    "pyspark/sql/types.py",
)

# Runs inside the subprocess: the notebook's directory (cwd) goes first on sys.path, as
# Databricks does for a notebook task, then the stubs. `-I` drops PYTHONPATH, user site,
# and the implicit cwd entry, so nothing else can make services/src importable.
_RUNNER = """\
import os, sys
stubs, prologue = sys.argv[1], sys.argv[2]
sys.path[:0] = [os.getcwd(), stubs]
namespace = {"__name__": "__main__"}
exec(compile(open(prologue).read(), "<notebook>", "exec"), namespace)
assert "__file__" not in namespace
"""

# Jobs whose opening statements import a sibling module from services/src.
SIBLING_IMPORT_JOBS = [job for job in JOBS if job != "migrate"]


def _run_prologue(job, cwd, tmp_path):
    """Exec the job's statements before its first dbutils/spark use, like a notebook task."""
    stubs = tmp_path / "stubs"
    for rel in _STUB_FILES:
        (stubs / rel).parent.mkdir(parents=True, exist_ok=True)
        (stubs / rel).write_text(_STUB)
    body = []
    for node in _tree(job).body:
        if re.search(r"\b(dbutils|spark)\b", ast.unparse(node)):
            break
        body.append(node)
    prologue = tmp_path / "prologue.py"
    prologue.write_text(ast.unparse(ast.Module(body=body, type_ignores=[])))
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [sys.executable, "-I", "-c", _RUNNER, str(stubs), str(prologue)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("job", JOBS)
def test_import_prologue_runs_from_notebook_dir(job, tmp_path):
    result = _run_prologue(job, SRC, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("job", SIBLING_IMPORT_JOBS)
def test_import_prologue_fails_outside_notebook_dir(job, tmp_path):
    """Negative control: from services/, sibling imports must not resolve."""
    result = _run_prologue(job, SERVICES, tmp_path)
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


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


def test_migrate_serving_principal_is_bundle_parameterized():
    bundle_source = (SERVICES / "databricks.yml").read_text()
    sp_role_block = re.search(r"(?ms)^  sp_role:\n(?P<body>(?:    .*\n)+)", bundle_source)
    assert sp_role_block is not None
    default = re.search(r"(?m)^    default: (?P<value>\S+)$", sp_role_block["body"])
    assert default is not None
    serving_principal = default["value"]
    migrate_source = (SRC / "migrate.py").read_text()

    assert serving_principal == "9779e0a0-0dc3-4f60-8746-ae1445b27c6e"
    assert serving_principal not in migrate_source
    assert 'dbutils.widgets.text("sp_role", "")' in migrate_source
    assert 'dbutils.widgets.get("sp_role").strip()' in migrate_source


def test_no_cache_or_persist_on_serverless():
    offenders = [
        p.name for p in SRC.glob("*.py") if re.search(r"\.(cache|persist)\(", p.read_text())
    ]
    assert offenders == []


@pytest.mark.parametrize("job", ["worker", "consumers"])
def test_kafka_error_messages_are_never_committed(job):
    """No `if msg.error():` branch in a consuming job commits an offset."""
    branches = [
        node
        for node in ast.walk(_tree(job))
        if isinstance(node, ast.If) and "msg.error()" in ast.unparse(node.test)
    ]
    source = (SRC / f"{job}.py").read_text()
    assert branches or "worker_core.handle_message(" in source
    for node in branches:
        assert ".commit(" not in "\n".join(ast.unparse(n) for n in node.body)
