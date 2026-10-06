import os

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "onebill-test"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Fake credentials so nothing can ever reach a real AWS account from tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "onebill-test")
    monkeypatch.setenv("POWERTOOLS_TRACE_DISABLED", "true")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"


@pytest.fixture
def table():
    """A moto-backed table with the same keys and GSIs as DataStack."""

    def gsi(name):
        return {
            "IndexName": name,
            "KeySchema": [
                {"AttributeName": f"{name}PK", "KeyType": "HASH"},
                {"AttributeName": f"{name}SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        tbl = ddb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            GlobalSecondaryIndexes=[gsi("GSI1"), gsi("GSI2")],
            BillingMode="PAY_PER_REQUEST",
        )
        yield tbl
