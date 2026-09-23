from pathlib import Path

import yaml


def test_bootstrap_full_refreshes_streaming_state():
    bundle = yaml.safe_load((Path(__file__).parents[1] / "databricks.yml").read_text())
    tasks = bundle["resources"]["jobs"]["bootstrap"]["tasks"]
    medallion = next(task for task in tasks if task["task_key"] == "medallion")

    assert medallion["pipeline_task"]["full_refresh"] is True
