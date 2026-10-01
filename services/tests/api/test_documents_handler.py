import importlib
import json
from dataclasses import dataclass

import boto3
import pytest

BUCKET = "onetally-docs-test"


@dataclass
class FakeLambdaContext:
    function_name: str = "documents"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:documents"
    aws_request_id: str = "req-1"


def http_event(method, path, claims, body=None, query=None, path_params=None):
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "queryStringParameters": query,
        "pathParameters": path_params,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "api",
            "domainName": "api.example.com",
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "1.2.3.4"},
            "requestId": "req-1",
            "routeKey": f"{method} {path}",
            "stage": "$default",
            "authorizer": {"jwt": {"claims": claims, "scopes": []}},
        },
    }


@pytest.fixture
def handler(table, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    import api.documents.handler

    return importlib.reload(api.documents.handler)


def call(handler, event):
    response = handler.handler(event, FakeLambdaContext())
    return response["statusCode"], json.loads(response["body"])


UPLOADER = {"sub": "user-a", "cognito:groups": "[uploaders]"}
NEW_DOC = {"filename": "receipt.jpg", "contentType": "image/jpeg", "sizeBytes": 123456}


def test_post_documents_creates_upload(handler):
    status, body = call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))

    assert status == 201
    assert body["upload"]["fields"]["key"].startswith("uploads/user-a/")


def test_post_documents_requires_uploaders_group(handler):
    status, body = call(handler, http_event("POST", "/documents", {"sub": "user-a"}, NEW_DOC))

    assert status == 403
    assert "invite-only" in body["message"]


def test_post_documents_validates_body(handler):
    status, _ = call(handler, http_event("POST", "/documents", UPLOADER, {"filename": "x.jpg"}))

    assert status == 400


def test_user_id_comes_only_from_claims(handler):
    body = NEW_DOC | {"userId": "user-b"}

    _, created = call(handler, http_event("POST", "/documents", UPLOADER, body))

    assert created["upload"]["fields"]["key"].startswith("uploads/user-a/")


def test_get_documents_lists_mine(handler):
    call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))

    status, body = call(handler, http_event("GET", "/documents", UPLOADER))

    assert status == 200
    assert len(body["documents"]) == 1


def test_get_other_users_document_is_404(handler):
    _, created = call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))
    doc_id = created["docId"]
    other = {"sub": "user-b"}

    status, _ = call(
        handler,
        http_event("GET", f"/documents/{doc_id}", other, path_params={"docId": doc_id}),
    )

    assert status == 404


def test_get_obligations_default_window(handler):
    status, body = call(handler, http_event("GET", "/obligations", UPLOADER))

    assert status == 200
    assert body["obligations"] == []


def test_missing_sub_is_401(handler):
    status, _ = call(handler, http_event("GET", "/documents", {}))

    assert status == 401
