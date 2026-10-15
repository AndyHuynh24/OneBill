"""Semantic search over a user's bills (RAG): one short text per bill, embedded with Amazon Titan
Text Embeddings V2, stored in Amazon S3 Vectors.

Every vector carries its owner's `userId` as metadata, and every query filters on the caller's
id, so a search can never return another user's bill. Keys are `<userId>#<docId>`: re-indexing
overwrites, and deleting is exact.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from shared.pii import redact

EMBED_MODEL = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 512  # short texts: half the storage of 1024, same quality for this
TOP_K = 5
MAX_DISTANCE = 0.75  # cosine distance: further than this isn't really a match
MAX_TEXT = 2000
TEXT_KINDS = frozenset({"item", "discount", "fee"})


def document_text(doc: dict[str, Any]) -> str | None:
    """`vendor | date | type` then one line per item with its price, redacted. None: nothing to
    find it by (still being read, or nothing read)."""
    lines = [
        f"{line['description']} {_dollars(int(line['amountCents']))}"
        for line in doc.get("lines") or []
        if line.get("kind") in TEXT_KINDS and line.get("description")
    ]
    head = [doc.get("vendor"), doc.get("documentDate"), doc.get("docType")]
    if not lines and not doc.get("vendor"):
        return None
    text = " | ".join(str(part) for part in head if part)
    if lines:
        text += "\n" + "\n".join(lines)
    return redact(text[:MAX_TEXT]).text


def embed(bedrock: Any, text: str) -> list[float]:
    response = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": DIMENSIONS, "normalize": True}),
        contentType="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def vector_key(user_id: str, doc_id: str) -> str:
    return f"{user_id}#{doc_id}"


def text_hash(text: str) -> str:
    """Stored with the vector: an edit that doesn't change the text (a paid tick, a trip) is
    skipped instead of embedded again."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def searcher(
    bedrock: Any, vectors: Any, index_arn: str, user_id: str
) -> Callable[[str], list[str]]:
    """A search function bound to one user: query text → their best-matching docIds."""

    def find(query: str) -> list[str]:
        response = vectors.query_vectors(
            indexArn=index_arn,
            topK=TOP_K,
            queryVector={"float32": embed(bedrock, redact(query).text)},
            filter={"userId": {"$eq": user_id}},
            returnMetadata=True,
            returnDistance=True,
        )
        return [
            hit["metadata"]["docId"]
            for hit in response.get("vectors", [])
            if hit.get("distance", 0) <= MAX_DISTANCE and "docId" in hit.get("metadata", {})
        ]

    return find


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"
