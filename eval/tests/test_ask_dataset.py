import json
from pathlib import Path

from moto import mock_aws

import ask_dataset
from run_ask_eval import make_table

CASES = json.loads((Path(__file__).parent.parent / "ask_cases.json").read_text())["cases"]


def test_the_dataset_loads_and_every_case_cites_bills_it_has(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        labels = ask_dataset.load(make_table())

    cited = {label for case in CASES for label in case.get("sources", [])}
    assert cited <= set(labels)
    assert len({case["id"] for case in CASES}) == len(CASES)
