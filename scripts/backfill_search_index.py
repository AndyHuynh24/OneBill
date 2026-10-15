"""Index bills that were read before search existed (run once per stage, from a laptop).

    cd services && uv run python ../scripts/backfill_search_index.py --stack OneBill-Staging
    cd services && uv run python ../scripts/backfill_search_index.py --stack OneBill --yes

Dry run by default: it counts the read bills and estimates the cost. With --yes it embeds each
one (Titan, ~$0.00002 each) and writes it to the stage's search index, exactly as the indexer
does. Writing a bill again overwrites it, so running twice is safe.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))

from assistant.search import document_text, embed, vector_key  # noqa: E402

READ = {"ready", "needs_review"}


def assistant_env(session: Any, stack: str) -> dict[str, str]:
    """TABLE_NAME and VECTOR_INDEX_ARN, read from the stage's deployed assistant Lambda."""
    resources = session.client("cloudformation").describe_stack_resources(StackName=f"{stack}-Api")
    name = next(
        r["PhysicalResourceId"]
        for r in resources["StackResources"]
        if r["ResourceType"] == "AWS::Lambda::Function"
        and r["LogicalResourceId"].startswith("AssistantFunction")
    )
    config = session.client("lambda").get_function_configuration(FunctionName=name)
    return config["Environment"]["Variables"]


def read_bills(table: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    scan: dict[str, Any] = {
        "FilterExpression": "begins_with(SK, :doc) AND #s IN (:ready, :review)",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":doc": "DOC#", ":ready": "ready", ":review": "needs_review"},
    }
    while True:
        page = table.scan(**scan)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", required=True, choices=["OneBill", "OneBill-Staging"])
    parser.add_argument("--profile", default="onebill-iam")
    parser.add_argument("--yes", action="store_true", help="embed and write (costs a little)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    env = assistant_env(session, args.stack)
    table = session.resource("dynamodb").Table(env["TABLE_NAME"])
    index_arn = env["VECTOR_INDEX_ARN"]
    bills = [b for b in read_bills(table) if b.get("status") in READ]
    texts = [(b, document_text(b)) for b in bills]
    texts = [(b, t) for b, t in texts if t]
    print(f"{args.stack}: {len(texts)} read bills to index (about ${len(texts) * 0.00002:.4f}).")
    if not args.yes:
        print("Dry run. Add --yes to index them.")
        return

    bedrock = session.client("bedrock-runtime")
    vectors = session.client("s3vectors")
    for count, (bill, text) in enumerate(texts, 1):
        user_id = bill["PK"].removeprefix("USER#")
        doc_id = bill["SK"].removeprefix("DOC#")
        metadata = {"userId": user_id, "docId": doc_id}
        if bill.get("documentDate"):
            metadata["date"] = bill["documentDate"]
        vectors.put_vectors(
            indexArn=index_arn,
            vectors=[
                {
                    "key": vector_key(user_id, doc_id),
                    "data": {"float32": embed(bedrock, text)},
                    "metadata": metadata,
                }
            ],
        )
        if count % 25 == 0:
            print(f"  {count}/{len(texts)}")
    print(f"Indexed {len(texts)} bills.")


if __name__ == "__main__":
    main()
