from types import SimpleNamespace
from unittest.mock import MagicMock

import register_agent


def test_registration_sets_candidate_and_never_prod(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(
        register_agent.mlflow,
        "register_model",
        lambda model_uri, name: SimpleNamespace(version="7"),
    )
    monkeypatch.setattr(register_agent, "MlflowClient", lambda **kwargs: client)

    result = register_agent.register_validated("runs:/run-id/agent")

    client.set_registered_model_alias.assert_called_once_with(
        register_agent.MODEL_NAME, "candidate", "7"
    )
    assert result["model_version"] == "7"
    assert result["alias"] == "candidate"
    assert "prod" not in str(client.set_registered_model_alias.call_args)


def test_packaged_code_modules_cover_every_local_import():
    # The served model only has CODE_MODULES; a local module missing from the list
    # (e.g. prior_claims_indexes, imported by retrieval) breaks the endpoint at load.
    import ast
    from pathlib import Path

    src = Path(register_agent.__file__).resolve().parent
    local = {p.stem for p in src.glob("*.py")}
    packaged = {"agent", *(m.removesuffix(".py") for m in register_agent.CODE_MODULES)}
    for module in packaged:
        tree = ast.parse((src / f"{module}.py").read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            elif isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            for name in names:
                if name in local:
                    assert name in packaged, f"{module}.py imports unpackaged {name}"
    assert "prior_claims_indexes" in packaged
