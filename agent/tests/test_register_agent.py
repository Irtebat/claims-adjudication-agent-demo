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
