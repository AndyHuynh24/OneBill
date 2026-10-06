import importlib
import json
from dataclasses import dataclass

import boto3
import pytest

BUCKET = "onebill-docs-test"


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


UPLOADER = {"sub": "user-a", "cognito:groups": "[owners]"}
NEW_DOC = {"filename": "receipt.jpg", "contentType": "image/jpeg", "sizeBytes": 123456}


def test_post_documents_creates_upload(handler):
    status, body = call(handler, http_event("POST", "/documents", UPLOADER, NEW_DOC))

    assert status == 201
    assert body["upload"]["fields"]["key"].startswith("uploads/user-a/")


def test_post_documents_is_open_to_anyone_signed_in(handler):
    status, _ = call(handler, http_event("POST", "/documents", {"sub": "user-a"}, NEW_DOC))

    assert status == 201


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
        "expectedUpdatedAt": "2026-10-01T18:00:00+00:00",
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


# --- subscriptions and the Gmail connection (signed in) -------------------------------------


def _save_netflix(table, user="user-a"):
    from datetime import UTC, date, datetime

    from subscriptions.parse import ParsedSubscription
    from subscriptions.store import save_subscription

    sub = ParsedSubscription(
        service="Apple Music",
        service_key="apple music",
        plan=None,
        amount_cents=1099,
        currency="USD",
        cycle="monthly",
        status="charged",
        charge_date=date(2026, 10, 1),
        period_end=None,
        next_renewal=date(2026, 11, 1),
    )
    save_subscription(table, user, sub, date(2026, 10, 1), datetime(2026, 10, 1, tzinfo=UTC))


def test_get_subscriptions_lists_mine_with_the_gmail_connection(handler, table):
    _save_netflix(table)
    _save_netflix(table, user="user-b")

    status, body = call(handler, http_event("GET", "/subscriptions", {"sub": "user-a"}))

    assert status == 200
    assert [s["service"] for s in body["subscriptions"]] == ["Apple Music"]
    assert body["connection"] == {
        "connected": False,
        "createdAt": None,
        "expiresOn": None,
        "lastUsedAt": None,
    }


def test_patch_subscription_cancels_it(handler, table):
    _save_netflix(table)
    event = http_event(
        "PATCH",
        "/subscriptions/apple%20music",  # the key has a space; the app URL-encodes it
        {"sub": "user-a"},
        body={"action": "cancel"},
        path_params={"key": "apple music"},
    )

    status, body = call(handler, event)

    assert status == 200
    assert body["status"] == "cancelled"


def test_patch_someone_elses_subscription_is_404(handler, table):
    _save_netflix(table, user="user-b")
    event = http_event(
        "PATCH", "/subscriptions/apple%20music", {"sub": "user-a"}, body={"action": "cancel"}
    )

    assert call(handler, event)[0] == 404


def test_patch_with_an_invalid_edit_is_400(handler, table):
    _save_netflix(table)
    event = http_event(
        "PATCH", "/subscriptions/apple%20music", {"sub": "user-a"}, body={"action": "nope"}
    )

    assert call(handler, event)[0] == 400


def test_create_and_revoke_the_ingest_key(handler, table):
    status, body = call(handler, http_event("POST", "/ingest/keys", UPLOADER))
    assert status == 201
    assert body["key"].startswith("obk_")

    _, listed = call(handler, http_event("GET", "/subscriptions", UPLOADER))
    assert listed["connection"]["connected"] is True
    assert "key" not in json.dumps(listed["connection"])  # never shown again

    response = handler.handler(http_event("DELETE", "/ingest/keys", UPLOADER), FakeLambdaContext())
    assert response["statusCode"] == 204


def test_only_the_owner_can_connect_gmail(handler):
    status, _ = call(handler, http_event("POST", "/ingest/keys", {"sub": "user-b"}))

    assert status == 403


def test_delete_subscription_and_the_reminders_switch(handler, table):
    _save_netflix(table)
    path = "/subscriptions/apple%20music"

    status, body = call(
        handler,
        http_event("PATCH", path, {"sub": "user-a"}, body={"action": "reminders", "on": False}),
    )
    assert (status, body["remindersOff"]) == (200, True)
    assert (
        call(
            handler,
            http_event("PATCH", path, {"sub": "user-a"}, body={"action": "reminders", "on": "no"}),
        )[0]
        == 400
    )

    response = handler.handler(http_event("DELETE", path, {"sub": "user-a"}), FakeLambdaContext())
    assert response["statusCode"] == 204
    assert call(handler, http_event("DELETE", path, {"sub": "user-a"}))[0] == 404


def test_delete_document_and_reminder_routes_are_scoped_to_the_caller(handler):
    other = "1790000000000aaaaaaaa"

    assert call(handler, http_event("DELETE", f"/documents/{other}", {"sub": "user-a"}))[0] == 404
    assert call(handler, http_event("DELETE", f"/obligations/{other}", {"sub": "user-a"}))[0] == 404


def test_post_subscriptions_adds_one_by_hand(handler):
    rent = {
        "service": "Rent",
        "category": "bill",
        "amountCents": 210000,
        "nextRenewal": "2026-11-01",
    }

    status, body = call(handler, http_event("POST", "/subscriptions", {"sub": "user-a"}, body=rent))
    assert (status, body["category"], body["source"]) == (201, "bill", "manual")

    assert (
        call(handler, http_event("POST", "/subscriptions", {"sub": "user-a"}, body=rent))[0] == 409
    )
    assert (
        call(
            handler, http_event("POST", "/subscriptions", {"sub": "user-a"}, body={"service": ""})
        )[0]
        == 400
    )
