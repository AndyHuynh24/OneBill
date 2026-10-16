"""Run Ask OneBill (the real agent loop, prompt, and tools) on fixed questions and score it.

    make eval-ask                      # dry run: the cases, the model, and the estimated cost
    make eval-ask ARGS="--yes"         # calls Bedrock (Claude Haiku and Titan embeddings)
    make eval-ask ARGS="--yes --only safety"

The data is the fake dataset in ask_dataset.py, in a local moto table, so no real user's bills
are read. Search (RAG) uses real Titan embeddings and OneBill's own indexing and search code, with
an in-memory stand-in for S3 Vectors that filters by user the same way. Per-case answers go to
eval/out/; the report goes to eval/results/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT / "services"))

from assistant import loop, search, tools  # noqa: E402
from assistant.prompt import PROMPT_VERSION  # noqa: E402

import ask_dataset  # noqa: E402
from ask_score import (  # noqa: E402
    INPUT_PER_M,
    OUTPUT_PER_M,
    render_markdown,
    score_case,
    summarize,
)

CASES = EVAL_DIR / "ask_cases.json"
OUT = EVAL_DIR / "out"
RESULTS = EVAL_DIR / "results"
INDEX = "eval-index"
EST_TOKENS = (4000, 200)  # per question, from staging: ~3,000 in / ~150 out on average
WORST_FACTOR = 2.0


def make_table() -> Any:
    """A moto table with the same keys and indexes as DataStack (see services/tests/conftest)."""
    import boto3

    def gsi(name: str) -> dict[str, Any]:
        keys = [("PK", "HASH"), ("SK", "RANGE")]
        return {
            "IndexName": name,
            "KeySchema": [{"AttributeName": f"{name}{k}", "KeyType": t} for k, t in keys],
            "Projection": {"ProjectionType": "ALL"},
        }

    return boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName="onebill-eval",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": n, "AttributeType": "S"}
            for n in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
        ],
        GlobalSecondaryIndexes=[gsi("GSI1"), gsi("GSI2")],
        BillingMode="PAY_PER_REQUEST",
    )


class MemoryVectors:
    """Stands in for the S3 Vectors client, in memory: the same calls the indexer and the search
    make (put, get, delete, query with an equality filter, cosine distance). The real
    search.index_bill and search.searcher run against it."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}

    def put_vectors(self, indexArn: str, vectors: list[dict[str, Any]]) -> dict:  # noqa: N803
        self.stored |= {v["key"]: v for v in vectors}
        return {}

    def get_vectors(self, indexArn: str, keys: list[str], returnMetadata: bool) -> dict:  # noqa: N803
        return {"vectors": [self.stored[k] for k in keys if k in self.stored]}

    def delete_vectors(self, indexArn: str, keys: list[str]) -> dict:  # noqa: N803
        for key in keys:
            self.stored.pop(key, None)
        return {}

    def query_vectors(self, indexArn, topK, queryVector, filter, **_) -> dict:  # noqa: A002, ANN001, N803
        q = queryVector["float32"]
        wanted = {k: cond["$eq"] for k, cond in filter.items()}
        hits = [
            {
                "key": v["key"],
                "metadata": v["metadata"],
                "distance": 1 - sum(a * b for a, b in zip(q, v["data"]["float32"], strict=True)),
            }
            for v in self.stored.values()
            if all(v["metadata"].get(k) == want for k, want in wanted.items())
        ]
        return {"vectors": sorted(hits, key=lambda h: h["distance"])[:topK]}


def build_index(bedrock: Any, table: Any, users: list[str]) -> MemoryVectors:
    """Index every read bill of these users, the way the indexer Lambda does."""
    index = MemoryVectors()
    for user in users:
        page = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :p)",
            ExpressionAttributeValues={":pk": f"USER#{user}", ":p": "DOC#"},
        )
        for doc in page["Items"]:
            if doc.get("status") in {"ready", "needs_review"}:
                search.index_bill(bedrock, index, INDEX, user, doc)
    return index


def run_case(bedrock: Any, model: str, ctx: tools.Context, case: dict[str, Any]) -> dict[str, Any]:
    used: list[str] = []
    real_run = tools.run

    def recording(ctx: tools.Context, name: str, args: dict[str, Any]) -> dict[str, Any]:
        used.append(name)
        return real_run(ctx, name, args)

    tools.run = recording  # the loop calls tools.run: record which tools it picked
    try:
        started = time.monotonic()
        answer = loop.ask(bedrock, model, ctx, case["question"], case.get("history", []))
        seconds = time.monotonic() - started
    finally:
        tools.run = real_run
    return {"answer": answer, "seconds": seconds, "tools": used}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", default="onebill-iam")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model", help="Bedrock model ID (default: bedrockModelId in cdk.json)")
    parser.add_argument("--only", help="Only cases whose id starts with this")
    parser.add_argument("--yes", action="store_true", help="Actually call Bedrock (costs money)")
    args = parser.parse_args()

    model = (
        args.model
        or json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]["bedrockModelId"]
    )
    cases = json.loads(CASES.read_text())["cases"]
    if args.only:
        cases = [c for c in cases if c["id"].startswith(args.only)]
    per_question = (EST_TOKENS[0] * INPUT_PER_M + EST_TOKENS[1] * OUTPUT_PER_M) / 1e6
    print(f"{len(cases)} cases · model {model} · prompt {PROMPT_VERSION}")
    print(
        f"Estimated cost: ~${len(cases) * per_question:.2f} "
        f"(≤ ${len(cases) * per_question * WORST_FACTOR:.2f}; embeddings < $0.01)"
    )
    if not args.yes:
        print("Dry run. Add --yes to call Bedrock.")
        return 0

    import boto3
    from moto import mock_aws

    # Only DynamoDB is faked; Bedrock calls go to AWS with the real profile's credentials.
    config = {
        "core": {
            "mock_credentials": False,
            "passthrough": {"urls": [rf"https://bedrock-runtime\.{args.region}\.amazonaws\.com.*"]},
        }
    }
    bedrock = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "bedrock-runtime"
    )
    with mock_aws(config=config):  # type: ignore[arg-type]
        # A real account has tables; moto starts empty. Stop if DynamoDB isn't being faked.
        if boto3.client("dynamodb", region_name="us-east-1").list_tables()["TableNames"]:
            print("DynamoDB isn't mocked: stopping before anything is written.")
            return 1
        table = make_table()
        labels = ask_dataset.load(table)
        by_id = {doc_id: label for label, doc_id in labels.items()}
        index = build_index(bedrock, table, [ask_dataset.USER, ask_dataset.OTHER_USER])
        ctx = tools.Context(
            table=table,
            user_id=ask_dataset.USER,
            today=ask_dataset.TODAY,
            search=search.searcher(bedrock, index, INDEX, ask_dataset.USER),
        )
        rows = []
        for case in cases:
            result = run_case(bedrock, model, ctx, case)
            answer = result["answer"]
            sources = [by_id.get(d, d) for d in answer.sources]
            score = score_case(case, answer.text, sources, result["tools"])
            rows.append(
                {
                    "id": case["id"],
                    "kind": case["kind"],
                    "question": case["question"],
                    "answer": answer.text,
                    "sources": sources,
                    "tools": result["tools"],
                    "score": score,
                    "seconds": round(result["seconds"], 2),
                    "calls": answer.calls,
                    "input_tokens": answer.input_tokens,
                    "output_tokens": answer.output_tokens,
                    "stopped": answer.stopped,
                }
            )
            mark = "✓" if score["correct"] else "✗"
            print(f"  {mark} {case['id']:<26} {result['seconds']:4.1f}s  {score['failures'] or ''}")

    summary = summarize(rows)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    report = render_markdown(summary, rows, f"{model} · prompt {PROMPT_VERSION} · {stamp}")
    print()
    print(report)
    OUT.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    (OUT / f"ask-{stamp}.json").write_text(json.dumps(rows, indent=2) + "\n")
    (RESULTS / f"ask-{stamp}.md").write_text(report)
    print(f"Answers: eval/out/ask-{stamp}.json · report: eval/results/ask-{stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
