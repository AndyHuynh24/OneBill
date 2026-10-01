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


def test_get_documents_includes_review_stats(handler):
    _, body = call(handler, http_event("GET", "/documents", UPLOADER))

    assert body["review"] == {"reviewed": 0, "acceptedUnchanged": 0, "acceptanceRate": None}


def test_patch_review_of_a_pending_document_is_400(handler):
    _, created = call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))
    doc_id = created["docId"]

    status, body = call(
        handler,
        http_event(
            "PATCH",
            f"/documents/{doc_id}",
            UPLOADER,
            {"action": "accept"},
            path_params={"docId": doc_id},
        ),
    )

    assert status == 400
    assert "processed" in body["message"]


def test_patch_review_of_someone_elses_document_is_404(handler):
    _, created = call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))
    doc_id = created["docId"]

    status, _ = call(
        handler,
        http_event("PATCH", f"/documents/{doc_id}", {"sub": "user-b"}, {"action": "accept"}),
    )

    assert status == 404


def test_get_obligations_default_window(handler):
    status, body = call(handler, http_event("GET", "/obligations", UPLOADER))

    assert status == 200
    assert body["obligations"] == []


def test_missing_sub_is_401(handler):
    status, _ = call(handler, http_event("GET", "/documents", {}))

    assert status == 401


@pytest.mark.parametrize("days", ["²", "-1", "abc", "1000"])
def test_invalid_window_is_400_not_500(handler, days):
    status, _ = call(
        handler, http_event("GET", "/obligations", UPLOADER, query={"withinDays": days})
    )

    assert status == 400


def test_get_friends_lists_only_mine(handler, table):
    from datetime import UTC, datetime

    from split.friends import remember_friends

    remember_friends(table, "user-a", ["Bao", "Linh"], datetime(2026, 10, 1, tzinfo=UTC))
    remember_friends(table, "user-b", ["Stranger"], datetime(2026, 10, 1, tzinfo=UTC))

    status, body = call(handler, http_event("GET", "/friends", {"sub": "user-a"}))

    assert status == 200
    assert sorted(f["name"] for f in body["friends"]) == ["Bao", "Linh"]


def test_delete_friend_removes_the_chip(handler, table):
    from datetime import UTC, datetime

    from split.friends import friend_id, list_friends, remember_friends

    remember_friends(table, "user-a", ["Bao"], datetime(2026, 10, 1, tzinfo=UTC))
    fid = friend_id("Bao")
    event = http_event(
        "DELETE", f"/friends/{fid}", {"sub": "user-a"}, path_params={"friendId": fid}
    )

    response = handler.handler(event, FakeLambdaContext())

    assert response["statusCode"] == 204
    assert list_friends(table, "user-a") == []


def test_delete_friend_with_a_malformed_id_is_404(handler):
    event = http_event("DELETE", "/friends/PROFILE", {"sub": "user-a"})

    status, _ = call(handler, event)

    assert status == 404


def test_put_split_saves_and_a_stale_resave_is_409(handler, table):
    lines = [
        {"row": 1, "description": "A", "kind": "item", "amountCents": 1000},
        {"row": 2, "description": "B", "kind": "item", "amountCents": 500},
        {"row": 3, "description": "TOTAL", "kind": "total", "amountCents": 1500},
    ]
    doc_id = "1790000000000aaaaaaaa"
    table.put_item(
        Item={
            "PK": "USER#user-a",
            "SK": f"DOC#{doc_id}",
            "docId": doc_id,
            "status": "ready",
            "totalCents": 1500,
            "lines": lines,
            "updatedAt": "2026-10-01T18:00:00+00:00",
        }
    )
    split = {
        "members": [{"name": "Me", "self": True}, {"name": "Bao"}],
        "assignments": {"1": [0], "2": [1]},
        "charges": {},
        "expectedVersion": 0,
    }
    path = f"/documents/{doc_id}/split"

    def put():
        event = http_event(
            "PUT", path, {"sub": "user-a"}, body=split, path_params={"docId": doc_id}
        )
        return call(handler, event)

    status, body = put()
    assert status == 200
    assert [s["totalCents"] for s in body["shares"]] == [1000, 500]

    status, body = put()  # same expectedVersion again: someone else already saved
    assert status == 409
    assert "Reload" in body["message"]
