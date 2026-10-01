"""Run the real extraction pipeline on labelled receipts and report per-field accuracy.

    make eval ARGS="--init-labels"   # add a label stub for every file in eval/receipts/
    make eval                        # dry run: shows what would run and the estimated cost
    make eval ARGS="--yes"           # calls Textract (cached per file) and Bedrock

Receipts, labels, the Textract cache, and per-receipt outputs stay local (gitignored).
Only the aggregate table in eval/results/ is meant to be committed.
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT / "services"))

from documents.extraction import PROMPT_VERSION, ExtractionError, extract  # noqa: E402
from documents.textract import parse_expense  # noqa: E402
from documents.validate import validate  # noqa: E402

from score import aggregate, render_markdown, score_receipt  # noqa: E402

RECEIPTS = EVAL_DIR / "receipts"
LABELS = RECEIPTS / "labels.json"
CACHE = EVAL_DIR / "cache"
OUT = EVAL_DIR / "out"
RESULTS = EVAL_DIR / "results"
SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}

# Anthropic list prices for Claude Haiku 4.5 (per million tokens); Bedrock's rate may differ.
INPUT_PER_M, OUTPUT_PER_M = 1.00, 5.00
TEXTRACT_PER_PAGE = 0.01
EST_TOKENS_PER_DOC = (3000, 1000)


def init_labels(receipts: Path = RECEIPTS, labels_path: Path = LABELS) -> list[str]:
    """Add an empty label for every receipt file that doesn't have one yet."""
    data = json.loads(labels_path.read_text()) if labels_path.exists() else {"receipts": []}
    known = {entry["file"] for entry in data["receipts"]}
    added = []
    for path in sorted(receipts.iterdir()):
        if path.suffix.lower() in SUFFIXES and path.name not in known:
            data["receipts"].append(
                {
                    "file": path.name,
                    "tricky": False,
                    "expected": {
                        "vendor": None,
                        "document_date": None,
                        "subtotal_cents": None,
                        "tax_cents": None,
                        "tip_cents": None,
                        "total_cents": None,
                        "item_count": None,
                        "deadlines": None,  # [] would mean "no deadlines"; null = not labelled
                    },
                }
            )
            added.append(path.name)
    labels_path.write_text(json.dumps(data, indent=2) + "\n")
    return added


def labelled_expectations(expected: dict[str, Any]) -> dict[str, Any]:
    """Drop fields left as null in the label stub, so they aren't scored."""
    return {k: v for k, v in expected.items() if v is not None}


def to_predicted(result: Any, outcome: Any) -> dict[str, Any]:
    return {
        "vendor": result.vendor,
        "document_date": result.document_date,
        "subtotal_cents": result.subtotal_cents,
        "tax_cents": result.tax_cents,
        "tip_cents": result.tip_cents,
        "total_cents": result.total_cents,
        "item_count": sum(1 for line in result.lines if line["kind"] == "item"),
        "lines": [
            {"description": line["description"], "kind": line["kind"]} for line in result.lines
        ],
        "deadlines": [
            {"type": o.type, "due_date": o.due_date.isoformat()} for o in result.obligations
        ],
        "status": result.status,
        "attempts": outcome.attempts,
    }


def _textract(client: Any, path: Path) -> tuple[dict[str, Any], bool]:
    """Textract result for a file, cached by content hash so prompt iterations don't re-pay OCR."""
    data = path.read_bytes()
    cached = CACHE / f"{hashlib.sha256(data).hexdigest()[:16]}.json"
    if cached.exists():
        return json.loads(cached.read_text()), True
    response = client.analyze_expense(Document={"Bytes": data})
    response.pop("ResponseMetadata", None)
    CACHE.mkdir(exist_ok=True)
    cached.write_text(json.dumps(response))
    return response, False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", default="onetally")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--model", help="Bedrock model/profile ID (default: bedrockModelId in infra/cdk.json)"
    )
    parser.add_argument("--limit", type=int, help="Only the first N receipts")
    parser.add_argument(
        "--init-labels", action="store_true", help="Add label stubs for new receipt files"
    )
    parser.add_argument("--yes", action="store_true", help="Actually call AWS (costs money)")
    args = parser.parse_args()

    if not RECEIPTS.exists():
        print(f"Put receipt photos in {RECEIPTS} first.")
        print("See eval/labels.example.json for the label format.")
        return 1
    if args.init_labels:
        added = init_labels()
        print(f"Added {len(added)} label stub(s) to {LABELS}.")
        print("Fill in the expected values (leave unknown ones null), then run the eval.")
        return 0

    model = (
        args.model
        or json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]["bedrockModelId"]
    )
    entries = json.loads(LABELS.read_text())["receipts"][: args.limit]
    est = len(entries) * (
        TEXTRACT_PER_PAGE
        + (EST_TOKENS_PER_DOC[0] * INPUT_PER_M + EST_TOKENS_PER_DOC[1] * OUTPUT_PER_M) / 1e6
    )
    print(f"{len(entries)} receipts · model {model} · prompt {PROMPT_VERSION}")
    print(f"Estimated cost: ≤ ${est:.2f} (Textract is cached per file after the first run)")
    if not args.yes:
        print("Dry run. Add --yes to call Textract and Bedrock.")
        return 0

    import boto3  # local import so tests and dry runs don't need AWS libraries configured

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    textract, bedrock = session.client("textract"), session.client("bedrock-runtime")

    rows, details = [], []
    textract_pages = 0
    tokens = {"inputTokens": 0, "outputTokens": 0}
    for entry in entries:
        path = RECEIPTS / entry["file"]
        if not path.exists():
            print(f"  skip {entry['file']}: file not found")
            continue
        response, cached = _textract(textract, path)
        if not cached:
            textract_pages += response.get("DocumentMetadata", {}).get("Pages", 1)
        expense = parse_expense([response])
        try:
            outcome = extract(bedrock, model, expense)
            result = validate(outcome, expense)
            predicted = to_predicted(result, outcome)
            for k in tokens:
                tokens[k] += outcome.usage[k]
        except ExtractionError as err:
            predicted = {"status": "failed", "attempts": 2, "error": str(err)}
        expected = labelled_expectations(entry["expected"])
        row = score_receipt(expected, predicted) | {
            "file": entry["file"],
            "tricky": entry.get("tricky", False),
            "status": predicted["status"],
            "attempts": predicted.get("attempts", 1),
        }
        rows.append(row)
        details.append(
            {"file": entry["file"], "expected": expected, "predicted": predicted, "score": row}
        )
        misses = [name for name, ok in row["fields"].items() if not ok]
        print(
            f"  {entry['file']}: {predicted['status']}"
            + (f" · wrong: {', '.join(misses)}" if misses else "")
        )

    now = datetime.now(UTC)
    summary = aggregate(rows)
    report = render_markdown(
        summary, {"prompt_version": PROMPT_VERSION, "model": model, "date": now.date().isoformat()}
    )
    RESULTS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    (RESULTS / f"{now.date().isoformat()}-{PROMPT_VERSION}.md").write_text(report)
    (OUT / f"{now:%Y%m%dT%H%M%S}.json").write_text(json.dumps(details, indent=2, default=str))
    cost = (
        textract_pages * TEXTRACT_PER_PAGE
        + (tokens["inputTokens"] * INPUT_PER_M + tokens["outputTokens"] * OUTPUT_PER_M) / 1e6
    )
    print()
    print(report)
    print(f"Actual cost ≈ ${cost:.3f} ({textract_pages} new Textract pages, {tokens} tokens)")
    print(f"Per-receipt details (local only): {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
