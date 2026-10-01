import importlib
import json
from dataclasses import dataclass

import pytest


@dataclass
class FakeLambdaContext:
    function_name: str = "me"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:me"
    aws_request_id: str = "req-1"


def http_event(path="/me", method="GET", claims=None, query=None, body=None):
    """Minimal API Gateway HTTP API (payload v2.0) event, as delivered after the JWT authorizer."""
    request_context = {
        "accountId": "123456789012",
        "apiId": "api",
        "domainName": "api.example.com",
        "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "1.2.3.4"},
        "requestId": "req-1",
        "routeKey": f"{method} {path}",
        "stage": "$default",
    }
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims, "scopes": []}}
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
        "requestContext": request_context,
    }


@pytest.fixture
def handler_module(table):
    """Import the handler inside the moto mock so its module-level boto3 resource is mocked."""
    import me.handler

    return importlib.reload(me.handler)


def call(handler_module, event):
    response = handler_module.handler(event, FakeLambdaContext())
    return response["statusCode"], json.loads(response["body"])


def test_returns_profile_for_authenticated_caller(handler_module):
    status, body = call(handler_module, http_event(claims={"sub": "abc-123"}))

    assert status == 200
    assert body["userId"] == "abc-123"
    assert body["baseCurrency"] == "USD"


def test_user_id_comes_only_from_jwt_claims(handler_module):
    event = http_event(
        claims={"sub": "abc-123"},
        query={"userId": "someone-else"},
        body={"userId": "someone-else"},
    )

    status, body = call(handler_module, event)

    assert status == 200
    assert body["userId"] == "abc-123"


def test_missing_claims_returns_401(handler_module):
    status, _ = call(handler_module, http_event(claims=None))

    assert status == 401


def test_empty_sub_returns_401(handler_module):
    status, _ = call(handler_module, http_event(claims={"sub": ""}))

    assert status == 401


def test_unknown_route_returns_404(handler_module):
    status, _ = call(handler_module, http_event(path="/nope", claims={"sub": "abc-123"}))

    assert status == 404
