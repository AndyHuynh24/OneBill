"""RAG: a bill becomes one short text → Titan embedding → S3 Vectors, always filtered by user."""

import io
import json

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from assistant import search

INDEX = "arn:aws:s3vectors:us-east-1:123456789012:bucket/onebill-vectors/index/docs"
VECTOR = [0.1] * 512


@pytest.fixture
def clients():
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
    vectors = boto3.client("s3vectors", region_name="us-east-1")
    with Stubber(bedrock) as b, Stubber(vectors) as v:
        yield bedrock, vectors, b, v
        b.assert_no_pending_responses()
        v.assert_no_pending_responses()


def expect_embedding(stub, text=ANY):
    body = json.dumps({"embedding": VECTOR, "inputTextTokenCount": 9}).encode()
    stub.add_response(
        "invoke_model",
        {"body": StreamingBody(io.BytesIO(body), len(body)), "contentType": "application/json"},
        {"modelId": search.EMBED_MODEL, "body": text, "contentType": "application/json"},
    )


BILL = {
    "PK": "USER#user-a",
    "SK": "DOC#d1",
    "docId": "d1",
    "status": "ready",
    "vendor": "BEST BUY",
    "documentDate": "2026-08-02",
    "docType": "receipt",
    "lines": [
        {"row": 1, "description": "USB C CBL 2M", "kind": "item", "amountCents": 1999},
        {"row": 2, "description": "TAX", "kind": "tax", "amountCents": 160},
        {"row": 3, "description": "TOTAL", "kind": "total", "amountCents": 2159},
    ],
}


def test_a_bill_becomes_one_short_text_with_its_items():
    text = search.document_text(BILL)

    assert text == "BEST BUY | 2026-08-02 | receipt\nUSB C CBL 2M 19.99"


def test_text_from_a_bill_is_redacted_before_it_is_embedded():
    bill = BILL | {"vendor": "Statement for jane@example.com"}

    assert "jane@example.com" not in search.document_text(bill)


def test_a_bill_with_nothing_read_has_no_text():
    assert search.document_text(BILL | {"lines": [], "vendor": None}) is None


def test_every_search_is_filtered_to_the_caller(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b)
    v.add_response(
        "query_vectors",
        {
            "vectors": [
                {"key": "user-a#d1", "distance": 0.12, "metadata": {"docId": "d1"}},
                {"key": "user-a#d7", "distance": 0.31, "metadata": {"docId": "d7"}},
            ],
            "distanceMetric": "cosine",
        },
        {
            "indexArn": INDEX,
            "topK": 5,
            "queryVector": {"float32": VECTOR},
            "filter": {"userId": {"$eq": "user-a"}},
            "returnMetadata": True,
            "returnDistance": True,
        },
    )

    find = search.searcher(bedrock, vectors, INDEX, "user-a")

    assert find("usb cable") == ["d1", "d7"]


def test_matches_too_far_away_are_dropped(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b)
    v.add_response(
        "query_vectors",
        {
            "vectors": [{"key": "user-a#d1", "distance": 0.9, "metadata": {"docId": "d1"}}],
            "distanceMetric": "cosine",
        },
        {
            "indexArn": INDEX,
            "topK": 5,
            "queryVector": ANY,
            "filter": ANY,
            "returnMetadata": True,
            "returnDistance": True,
        },
    )

    assert search.searcher(bedrock, vectors, INDEX, "user-a")("unrelated") == []


# --- the indexer (DynamoDB stream → S3 Vectors) --------------------------------------------------


def record(event, image=None, keys=None):
    from boto3.dynamodb.types import TypeSerializer

    ser = TypeSerializer()
    body = {
        "Keys": {
            k: ser.serialize(v) for k, v in (keys or {"PK": BILL["PK"], "SK": BILL["SK"]}).items()
        }
    }
    if image is not None:
        body["NewImage"] = {k: ser.serialize(v) for k, v in image.items()}
    return {"eventName": event, "dynamodb": body}


def run_indexer(monkeypatch, bedrock, vectors, records):
    from indexer import handler as indexer  # after the fake credentials (it makes clients)

    monkeypatch.setattr(indexer, "bedrock", bedrock)
    monkeypatch.setattr(indexer, "vectors", vectors)
    monkeypatch.setattr(indexer, "INDEX_ARN", INDEX)
    indexer.process(records)


def test_a_read_bill_is_indexed_under_its_owner(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    v.add_response(
        "get_vectors",
        {"vectors": []},  # not indexed yet
        {"indexArn": INDEX, "keys": ["user-a#d1"], "returnMetadata": True},
    )
    expect_embedding(b)
    v.add_response(
        "put_vectors",
        {},
        {
            "indexArn": INDEX,
            "vectors": [
                {
                    "key": "user-a#d1",
                    "data": {"float32": VECTOR},
                    "metadata": {
                        "userId": "user-a",
                        "docId": "d1",
                        "date": "2026-08-02",
                        "textHash": search.text_hash(search.document_text(BILL)),
                    },
                }
            ],
        },
    )

    run_indexer(monkeypatch, bedrock, vectors, [record("MODIFY", BILL)])


def test_a_bill_whose_text_is_unchanged_isnt_embedded_again(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    text_hash = search.text_hash(search.document_text(BILL))
    v.add_response(
        "get_vectors",
        {"vectors": [{"key": "user-a#d1", "metadata": {"textHash": text_hash}}]},
        {"indexArn": INDEX, "keys": ["user-a#d1"], "returnMetadata": True},
    )

    run_indexer(monkeypatch, bedrock, vectors, [record("MODIFY", BILL)])  # e.g. a paid tick


def test_a_search_query_is_redacted_before_it_is_embedded(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b, text=ANY)
    sent = []
    bedrock.meta.events.register(
        "before-parameter-build.bedrock-runtime.InvokeModel",
        lambda params, **_: sent.append(params),
    )
    v.add_response("query_vectors", {"vectors": [], "distanceMetric": "cosine"}, None)

    search.searcher(bedrock, vectors, INDEX, "user-a")("receipt for jane@example.com")

    assert "jane@example.com" not in sent[0]["body"]


def test_a_deleted_bill_leaves_the_index(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    v.add_response("delete_vectors", {}, {"indexArn": INDEX, "keys": ["user-a#d1"]})

    run_indexer(monkeypatch, bedrock, vectors, [record("REMOVE")])


def test_bills_still_being_read_and_other_items_are_skipped(clients, monkeypatch):
    bedrock, vectors, *_ = clients
    reading = BILL | {"status": "processing"}
    obligation = {"PK": "USER#user-a", "SK": "OBL#2026-10-20#x"}

    run_indexer(
        monkeypatch,
        bedrock,
        vectors,
        [record("MODIFY", reading), record("INSERT", obligation, keys=obligation)],
    )  # no calls queued: nothing is embedded or written
