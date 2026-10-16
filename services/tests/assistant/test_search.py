"""RAG: each item on a bill → a short text → Titan embedding → S3 Vectors, filtered by user."""

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


def test_each_item_becomes_its_own_short_text_with_the_store():
    assert search.item_texts(BILL) == [(1, "USB C CBL 2M | BEST BUY")]  # tax and total aren't


def test_item_text_is_redacted_before_it_is_embedded():
    bill = BILL | {"vendor": "Statement for jane@example.com"}

    assert "jane@example.com" not in search.item_texts(bill)[0][1]


def test_a_bill_with_no_items_has_nothing_to_index():
    assert search.item_texts(BILL | {"lines": []}) == []


def query_response(hits):
    return {"vectors": hits, "distanceMetric": "cosine"}


def test_every_search_is_filtered_to_the_caller_and_finds_items(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b)
    v.add_response(
        "query_vectors",
        query_response(
            [
                {"key": "user-a#d1#0", "distance": 0.12, "metadata": {"docId": "d1", "row": 1}},
                {"key": "user-a#d7#2", "distance": 0.31, "metadata": {"docId": "d7", "row": 4}},
            ]
        ),
        {
            "indexArn": INDEX,
            "topK": search.TOP_K,
            "queryVector": {"float32": VECTOR},
            "filter": {"userId": {"$eq": "user-a"}},
            "returnMetadata": True,
            "returnDistance": True,
        },
    )

    find = search.searcher(bedrock, vectors, INDEX, "user-a")

    assert find("usb cable") == [("d1", 1), ("d7", 4)]


def test_matches_too_far_away_or_without_a_row_are_dropped(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b)
    hits = [
        {"key": "user-a#d1#0", "distance": 0.9, "metadata": {"docId": "d1", "row": 1}},
        {"key": "user-a#d2", "distance": 0.1, "metadata": {"docId": "d2"}},  # a whole-bill vector
    ]
    v.add_response("query_vectors", query_response(hits), None)

    assert search.searcher(bedrock, vectors, INDEX, "user-a")("unrelated") == []


def test_a_search_query_is_redacted_before_it_is_embedded(clients):
    bedrock, vectors, b, v = clients
    expect_embedding(b, text=ANY)
    sent = []
    bedrock.meta.events.register(
        "before-parameter-build.bedrock-runtime.InvokeModel",
        lambda params, **_: sent.append(params),
    )
    v.add_response("query_vectors", query_response([]), None)

    search.searcher(bedrock, vectors, INDEX, "user-a")("receipt for jane@example.com")

    assert "jane@example.com" not in sent[0]["body"]


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


TWO_ITEMS = BILL | {
    "lines": [
        {"row": 1, "description": "USB C CBL 2M", "kind": "item", "amountCents": 1999},
        {"row": 2, "description": "CASE COVER", "kind": "item", "amountCents": 1499},
        {"row": 3, "description": "TOTAL", "kind": "total", "amountCents": 3498},
    ]
}
ALL_KEYS = [f"user-a#d1#{n}" for n in range(search.MAX_ITEMS)]


def expect_stored_hash(v, text_hash):
    found = [{"key": "user-a#d1#0", "metadata": {"textHash": text_hash}}] if text_hash else []
    v.add_response(
        "get_vectors",
        {"vectors": found},
        {"indexArn": INDEX, "keys": ["user-a#d1#0"], "returnMetadata": True},
    )


def item_vector(n, row, digest):
    return {
        "key": f"user-a#d1#{n}",
        "data": {"float32": VECTOR},
        "metadata": {"userId": "user-a", "docId": "d1", "row": row, "textHash": digest},
    }


def test_each_item_of_a_read_bill_is_indexed_under_its_owner(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    digest = search.bill_hash(TWO_ITEMS)
    expect_stored_hash(v, None)  # not indexed yet
    expect_embedding(b)
    expect_embedding(b)
    v.add_response(
        "put_vectors",
        {},
        {"indexArn": INDEX, "vectors": [item_vector(0, 1, digest), item_vector(1, 2, digest)]},
    )

    run_indexer(monkeypatch, bedrock, vectors, [record("MODIFY", TWO_ITEMS)])


def test_a_bill_whose_text_is_unchanged_isnt_embedded_again(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    expect_stored_hash(v, search.bill_hash(BILL))

    run_indexer(monkeypatch, bedrock, vectors, [record("MODIFY", BILL)])  # e.g. a paid tick


def test_an_edited_bill_is_reindexed_and_its_dropped_items_removed(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    expect_stored_hash(v, "an-older-version")
    expect_embedding(b)
    v.add_response(
        "put_vectors",
        {},
        {"indexArn": INDEX, "vectors": [item_vector(0, 1, search.bill_hash(BILL))]},
    )
    v.add_response("delete_vectors", {}, {"indexArn": INDEX, "keys": ALL_KEYS[1:]})

    run_indexer(monkeypatch, bedrock, vectors, [record("MODIFY", BILL)])


def test_a_deleted_bill_leaves_the_index(clients, monkeypatch):
    bedrock, vectors, b, v = clients
    v.add_response("delete_vectors", {}, {"indexArn": INDEX, "keys": ALL_KEYS})

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
