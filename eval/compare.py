"""Compare three ways of reading a receipt on the same labelled photos:

    textract  Textract AnalyzeExpense alone: its summary fields and line-item rows, no model.
    llm       The model alone: the whole photo, one call, no OCR text (same tool schema and prompt
              rules, and the same code turns the lines into totals).
    hybrid    OneBill's pipeline: Textract text + photo tiles (PII blacked out) + Claude,
              with the code-checked repair pass. `hybrid-text` is the same without the tiles.

    cd services && uv run python ../eval/compare.py              # dry run with a cost estimate
    cd services && uv run python ../eval/compare.py --yes        # calls Textract and Bedrock
    cd services && uv run python ../eval/compare.py --dir ../eval/public/us50 ../eval/public/us50b \
        --rescore ../eval/out/compare-A.json ../eval/out/compare-B.json   # combined, no AWS calls

Defaults to eval/public/us50/ (labels.json next to the photos). Textract results are cached per
file in eval/cache/, so a second run only pays for the model calls.
"""

import argparse
import io
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent / "services"))
sys.path.insert(0, str(EVAL_DIR))

from documents.extraction import (  # noqa: E402
    _OCR_INTRO,
    SYSTEM_PROMPT,
    ExtractionError,
    ExtractionOutcome,
    _tool_use,
    _total_required,
    build_request,
    extract,
    parse_output,
)
from documents.image_render import model_images  # noqa: E402
from documents.reconcile import reconcile  # noqa: E402
from documents.textract import ExpenseResult, parse_expense  # noqa: E402
from documents.validate import validate  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from run_eval import (  # noqa: E402
    INPUT_PER_M,
    OUTPUT_PER_M,
    ROOT,
    TEXTRACT_PER_PAGE,
    _textract,
    labelled_expectations,
    to_predicted,
)
from score import SCALAR_FIELDS, score_receipt  # noqa: E402

SYSTEMS = ("textract", "llm", "hybrid-text", "hybrid")
LLM_ONLY_PROMPT = SYSTEM_PROMPT.replace(
    _OCR_INTRO, "You receive one document as a photo. Read every line from the photo.\n\n"
)
LLM_MAX_EDGE = 1568  # Claude downsizes anything larger, so sending more pixels gains nothing

_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"), ("m", "d", "y")),
    (re.compile(r"(\d{1,2})[- ]?([a-z]{3})[a-z]*[- ']*(\d{2,4})", re.I), ("d", "mon", "y")),
    (
        re.compile(r"([a-z]{3})[a-z]*\.? ?(\d{1,2})(?:st|nd|rd|th)?[,' ]+(\d{2,4})", re.I),
        ("mon", "d", "y"),
    ),
]


def normalize_date(text: str | None) -> str | None:
    """Textract gives the date as printed ("09/08/14", "Jun 26, 15", "Oct06'16"). Turning that
    into YYYY-MM-DD is code anyone would write on top of Textract, so the baseline gets it."""
    for pattern, order in _DATE_PATTERNS:
        for match in pattern.finditer(text or ""):
            parts = dict(zip(order, match.groups(), strict=True))
            month = _MONTHS.get(parts["mon"][:3].lower()) if "mon" in parts else int(parts["m"])
            year = int(parts["y"]) + (2000 if len(parts["y"]) == 2 else 0)
            try:
                return datetime(year, month or 0, int(parts["d"])).date().isoformat()
            except ValueError:
                continue
    return None


def textract_only(expense: ExpenseResult) -> dict[str, Any]:
    money = expense.summary_money
    return {
        "vendor": expense.vendor,
        "document_date": normalize_date(expense.date_text),
        "subtotal_cents": money.get("SUBTOTAL"),
        "tax_cents": money.get("TAX", 0),
        "tip_cents": money.get("GRATUITY", 0),
        "fees_cents": money.get("SERVICE_CHARGE", 0),
        "total_cents": money.get("TOTAL"),
        "item_count": sum(1 for r in expense.line_items if r.price_cents is not None),
        "status": "ready",
    }


def photo_for_llm(data: bytes) -> bytes:
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    image.thumbnail((LLM_MAX_EDGE, LLM_MAX_EDGE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def llm_only(bedrock: Any, model: str, data: bytes) -> tuple[dict[str, Any], dict[str, int], int]:
    request = build_request(model, "", images=[photo_for_llm(data)])
    request["system"] = [{"text": LLM_ONLY_PROMPT}]
    request["messages"][0]["content"][-1] = {"text": "Record the document in this photo."}
    usage = {"inputTokens": 0, "outputTokens": 0}
    started = time.monotonic()
    response = bedrock.converse(**request)
    ms = int((time.monotonic() - started) * 1000)
    document = parse_output(_tool_use(response, usage)["input"])
    rec = reconcile(document.lines, None, _total_required(document))
    outcome = ExtractionOutcome(document, rec, 1, usage, model, model_ms=[ms])
    return to_predicted(validate(outcome, ExpenseResult(pages=1)), outcome), usage, ms


def run_one(
    entry: dict[str, Any], folder: Path, clients: dict[str, Any], model: str, systems: list[str]
) -> dict[str, Any]:
    path = folder / entry["file"]
    data = path.read_bytes()
    response, cached = _textract(clients["textract"], path)
    expense = parse_expense([response])
    expected = labelled_expectations(entry["expected"])
    out: dict[str, Any] = {
        "file": entry["file"],
        "tricky": entry.get("tricky", False),
        "new_pages": 0 if cached else expense.pages,
        "systems": {},
    }
    for system in systems:
        usage, ms = {"inputTokens": 0, "outputTokens": 0}, 0
        try:
            if system == "textract":
                predicted = textract_only(expense)
            elif system == "llm":
                predicted, usage, ms = llm_only(clients["bedrock"], model, data)
            else:
                images = model_images(data, expense) if system == "hybrid" else []
                outcome = extract(clients["bedrock"], model, expense, images)
                predicted = to_predicted(validate(outcome, expense), outcome)
                usage, ms = outcome.usage, sum(outcome.model_ms)
        except ExtractionError as err:
            predicted = {"status": "failed", "error": str(err)}
        row = score_receipt(expected, predicted)
        out["systems"][system] = {
            "fields": row["fields"],
            "usage": usage,
            "ms": ms,
            "predicted": predicted,
        }
    return out


def _pct(correct: int, total: int) -> str:
    return f"{100 * correct / total:.0f}%" if total else "–"


def render(results: list[dict[str, Any]], systems: list[str], meta: dict[str, str]) -> str:
    """Aggregate numbers only (no vendors or amounts), so the table is safe to commit."""

    def tally(system: str, field: str | None = None, tricky: bool | None = None):
        right = total = 0
        for r in results:
            if tricky is not None and r["tricky"] != tricky:
                continue
            for name, ok in r["systems"][system]["fields"].items():
                if field is None or name == field:
                    total += 1
                    right += int(ok)
        return right, total

    def receipts_all_right(system: str) -> str:
        perfect = sum(all(r["systems"][system]["fields"].values()) for r in results)
        return _pct(perfect, len(results))

    head = " | ".join(systems)
    lines = [
        f"# Reading accuracy: Textract vs Claude vs both ({meta['date']})",
        "",
        f"- Data: {meta['data']} ({len(results)} receipts)",
        f"- Model: `{meta['model']}` · prompt `{meta['prompt']}`",
        "",
        f"| Field | {head} |",
        "|---|" + "---|" * len(systems),
    ]
    for name, label in SCALAR_FIELDS:
        n = tally(systems[0], name)[1]
        if n:
            cells = " | ".join(_pct(*tally(s, name)) for s in systems)
            lines.append(f"| {label} (n={n}) | {cells} |")
    lines.append(
        f"| **All fields** (n={tally(systems[0])[1]}) | "
        + " | ".join(f"**{_pct(*tally(s))}**" for s in systems)
        + " |"
    )
    lines.append(
        "| Plain receipts, all fields | "
        + " | ".join(_pct(*tally(s, tricky=False)) for s in systems)
        + " |"
    )
    lines.append(
        "| Tricky receipts, all fields | "
        + " | ".join(_pct(*tally(s, tricky=True)) for s in systems)
        + " |"
    )
    lines.append(
        "| Receipts with every field right | "
        + " | ".join(receipts_all_right(s) for s in systems)
        + " |"
    )
    model_ms = {
        s: [r["systems"][s]["ms"] for r in results if r["systems"][s]["ms"]] for s in systems
    }
    lines.append(
        "| Model time per receipt (median) | "
        + " | ".join(
            f"{statistics.median(v) / 1000:.1f} s" if v else "–" for v in model_ms.values()
        )
        + " |"
    )
    cost = []
    for s in systems:
        tokens = [r["systems"][s]["usage"] for r in results]
        model_cost = (
            sum(t["inputTokens"] * INPUT_PER_M + t["outputTokens"] * OUTPUT_PER_M for t in tokens)
            / 1e6
        )
        ocr = TEXTRACT_PER_PAGE * len(results) if s != "llm" else 0
        cost.append(f"${(model_cost + ocr) / len(results) * 1000:.2f}")
    lines.append("| Cost per 1,000 receipts | " + " | ".join(cost) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dir",
        type=Path,
        nargs="+",
        default=[EVAL_DIR / "public" / "us50"],
        help="One or more folders, each with photos and a labels.json",
    )
    parser.add_argument("--profile", default="onebill-iam")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model")
    parser.add_argument("--systems", default=",".join(SYSTEMS))
    parser.add_argument("--limit", type=int)
    # More than 1 has crashed Python (segfault) on macOS; Bedrock time is ~3 s a receipt anyway.
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--rescore",
        type=Path,
        nargs="+",
        help="Re-score saved eval/out/compare-*.json files with the current "
        "labels (after fixing a label): no AWS calls",
    )
    parser.add_argument("--yes", action="store_true", help="Actually call AWS (costs money)")
    args = parser.parse_args()

    systems = [s for s in args.systems.split(",") if s in SYSTEMS]
    sets = [json.loads((folder / "labels.json").read_text()) for folder in args.dir]
    labels = {
        "source": "; ".join(dict.fromkeys(str(s.get("source", "")) for s in sets)),
        "receipts": [
            e | {"folder": folder}
            for folder, s in zip(args.dir, sets, strict=True)
            for e in s["receipts"]
        ],
    }
    name = "+".join(folder.name for folder in args.dir)
    entries = labels["receipts"][: args.limit]
    model = (
        args.model
        or json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]["bedrockModelId"]
    )
    calls = sum(2 if s.startswith("hybrid") else 1 for s in systems if s != "textract")
    estimate = len(entries) * (
        TEXTRACT_PER_PAGE + calls * (5000 * INPUT_PER_M + 1200 * OUTPUT_PER_M) / 1e6
    )
    if args.rescore:
        results = [r for path in args.rescore for r in json.loads(path.read_text())]
        by_file = {e["file"]: e for e in labels["receipts"]}
        for r in results:
            expected = labelled_expectations(by_file[r["file"]]["expected"])
            for v in r["systems"].values():
                v["fields"] = score_receipt(expected, v["predicted"])["fields"]
        systems = [s for s in systems if s in results[0]["systems"]]
    print(f"{len(entries)} receipts · {', '.join(systems)} · {model}")
    print(f"Estimated cost: ≤ ${estimate:.2f} (Textract cached per file after the first run)")
    if not args.yes and not args.rescore:
        print("Dry run. Add --yes to call Textract and Bedrock.")
        return 0

    if args.rescore:
        return _report(results, systems, model, labels, name, save_details=False)

    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    retries = Config(retries={"max_attempts": 8, "mode": "adaptive"})
    clients = {
        "textract": session.client("textract", config=retries),
        "bedrock": session.client("bedrock-runtime", config=retries),
    }

    def work(entry: dict[str, Any]) -> dict[str, Any]:
        result = run_one(entry, entry["folder"], clients, model, systems)
        misses = {
            s: [f for f, ok in v["fields"].items() if not ok] for s, v in result["systems"].items()
        }
        print(
            f"  {entry['file']}: "
            + " · ".join(
                f"{s} {'ok' if not m else 'wrong ' + ','.join(m)}" for s, m in misses.items()
            ),
            flush=True,
        )
        return result

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(work, entries))

    return _report(results, systems, model, labels, name, save_details=True)


def _report(
    results: list[dict[str, Any]],
    systems: list[str],
    model: str,
    labels: dict[str, Any],
    name: str,
    save_details: bool,
) -> int:
    from documents.extraction import PROMPT_VERSION

    now = datetime.now(UTC)
    report = render(
        results,
        systems,
        {
            "date": now.date().isoformat(),
            "model": model,
            "prompt": PROMPT_VERSION,
            "data": labels["source"],
        },
    )
    (EVAL_DIR / "results").mkdir(exist_ok=True)
    (EVAL_DIR / "out").mkdir(exist_ok=True)
    (EVAL_DIR / "results" / f"{now.date().isoformat()}-compare-{name}.md").write_text(report)
    if save_details:
        (EVAL_DIR / "out" / f"compare-{now:%Y%m%dT%H%M%S}.json").write_text(
            json.dumps(results, indent=2, default=str)
        )
    pages = sum(r["new_pages"] for r in results)
    tokens = (
        sum(
            v["usage"]["inputTokens"] * INPUT_PER_M + v["usage"]["outputTokens"] * OUTPUT_PER_M
            for r in results
            for v in r["systems"].values()
        )
        / 1e6
    )
    print()
    print(report)
    if save_details:  # a rescore makes no AWS calls
        print(f"Cost of this run ≈ ${pages * TEXTRACT_PER_PAGE + tokens:.2f} ({pages} new pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
