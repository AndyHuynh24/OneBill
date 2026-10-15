"""Keep the search index in step with bills (DynamoDB stream → S3 Vectors).

The stream's filter only passes DOC# items that are read (ready / needs_review) and DOC#
deletions. A read bill is embedded and written under `<userId>#<docId>` (writing it again
overwrites, so retries are safe); a deleted one is removed. Failures retry, then go to the
pipeline's dead-letter queue.
"""

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer

from assistant.search import document_text, embed, text_hash, vector_key

logger = Logger()
bedrock = boto3.client("bedrock-runtime")
vectors = boto3.client("s3vectors")
INDEX_ARN = os.environ.get("VECTOR_INDEX_ARN", "")
READ_STATUSES = frozenset({"ready", "needs_review"})
_deserialize = TypeDeserializer().deserialize


@logger.inject_lambda_context
def handler(event: dict, context: object) -> None:
    process(event.get("Records", []))


def process(records: list[dict[str, Any]]) -> None:
    indexed = removed = 0
    for record in records:
        keys = {k: _deserialize(v) for k, v in record["dynamodb"]["Keys"].items()}
        if not str(keys.get("SK", "")).startswith("DOC#"):
            continue
        user_id = str(keys["PK"]).removeprefix("USER#")
        doc_id = str(keys["SK"]).removeprefix("DOC#")
        key = vector_key(user_id, doc_id)
        if record["eventName"] == "REMOVE":
            vectors.delete_vectors(indexArn=INDEX_ARN, keys=[key])
            removed += 1
            continue
        image = {k: _deserialize(v) for k, v in record["dynamodb"].get("NewImage", {}).items()}
        text = document_text(image) if image.get("status") in READ_STATUSES else None
        if text is None:
            continue
        digest = text_hash(text)
        if _indexed_hash(key) == digest:
            continue  # the searchable text didn't change (e.g. a paid tick): nothing to embed
        metadata = {"userId": user_id, "docId": doc_id}
        if image.get("documentDate"):
            metadata["date"] = image["documentDate"]
        metadata["textHash"] = digest
        vectors.put_vectors(
            indexArn=INDEX_ARN,
            vectors=[{"key": key, "data": {"float32": embed(bedrock, text)}, "metadata": metadata}],
        )
        indexed += 1
    # Counts only: no bill text, no user ids.
    logger.info("search_index_updated", extra={"indexed": indexed, "removed": removed})


def _indexed_hash(key: str) -> str | None:
    found = vectors.get_vectors(indexArn=INDEX_ARN, keys=[key], returnMetadata=True)
    for vector in found.get("vectors", []):
        return (vector.get("metadata") or {}).get("textHash")
    return None
