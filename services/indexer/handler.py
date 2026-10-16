"""Keep the search index in step with bills (DynamoDB stream → S3 Vectors).

The stream's filter only passes DOC# items that are read (ready / needs_review) and DOC#
deletions. Each item of a read bill is embedded and written under `<userId>#<docId>#<n>`
(writing again overwrites, so retries are safe); a deleted bill's items are removed. Failures
retry, then go to the pipeline's dead-letter queue.
"""

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.types import TypeDeserializer

from assistant.search import index_bill, unindex_bill

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
        if record["eventName"] == "REMOVE":
            unindex_bill(vectors, INDEX_ARN, user_id, doc_id)
            removed += 1
            continue
        image = {k: _deserialize(v) for k, v in record["dynamodb"].get("NewImage", {}).items()}
        if image.get("status") not in READ_STATUSES:
            continue
        # Unchanged searchable text (e.g. a paid tick) is skipped inside index_bill.
        if index_bill(bedrock, vectors, INDEX_ARN, user_id, image | {"docId": doc_id}):
            indexed += 1
    # Counts only: no bill text, no user ids.
    logger.info("search_index_updated", extra={"indexed": indexed, "removed": removed})
