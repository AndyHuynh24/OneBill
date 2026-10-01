"""Deterministic scoring for the extraction eval.

Extraction has exact answers (amounts, dates), so scoring is exact match, not an LLM judge.
Only labelled fields are scored, so a quick label (just the total) still counts.
"""

import re
from typing import Any

SCALAR_FIELDS = (
    ("vendor", "Vendor"),
    ("document_date", "Date"),
    ("subtotal_cents", "Subtotal"),
    ("tax_cents", "Tax"),
    ("tip_cents", "Tip"),
    ("total_cents", "Total"),
    ("item_count", "Item count"),
)


def _words(text: str | None) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower().replace("'", "")).split()


def vendors_match(expected: str, predicted: str | None) -> bool:
    """Case/punctuation-insensitive; tolerates store numbers and locations after the name."""
    want, got = _words(expected), _words(predicted)
    return bool(want) and got[: len(want)] == want


def _normalize(text: str) -> str:
    return " ".join(_words(text))


def score_receipt(expected: dict[str, Any], predicted: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, bool] = {}
    for name, _label in SCALAR_FIELDS:
        if name not in expected:
            continue
        if name == "vendor":
            fields[name] = vendors_match(expected[name], predicted.get(name))
        else:
            fields[name] = expected[name] == predicted.get(name)

    row: dict[str, Any] = {"fields": fields}
    if "deadlines" in expected:
        want = {(d["type"], d["due_date"]) for d in expected["deadlines"]}
        got = {(d["type"], d["due_date"]) for d in predicted.get("deadlines", [])}
        row["deadlines"] = {"tp": len(want & got), "fp": len(got - want), "fn": len(want - got)}
    if "lines" in expected:
        predicted_kinds = {
            _normalize(line["description"]): line["kind"] for line in predicted.get("lines", [])
        }
        correct = sum(
            1
            for line in expected["lines"]
            if predicted_kinds.get(_normalize(line["description"])) == line["kind"]
        )
        row["line_kinds"] = {"correct": correct, "total": len(expected["lines"])}
    return row


def _empty_bucket() -> dict[str, Any]:
    return {
        "count": 0,
        "fields": {name: {"correct": 0, "total": 0} for name, _ in SCALAR_FIELDS},
        "deadlines": {"tp": 0, "fp": 0, "fn": 0},
        "line_kinds": {"correct": 0, "total": 0},
        "needs_review": 0,
        "repaired": 0,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets = {"all": _empty_bucket(), "plain": _empty_bucket(), "tricky": _empty_bucket()}
    for row in rows:
        for key in ("all", "tricky" if row.get("tricky") else "plain"):
            bucket = buckets[key]
            bucket["count"] += 1
            for name, ok in row["fields"].items():
                bucket["fields"][name]["total"] += 1
                bucket["fields"][name]["correct"] += int(ok)
            for k in ("tp", "fp", "fn"):
                bucket["deadlines"][k] += row.get("deadlines", {}).get(k, 0)
            for k in ("correct", "total"):
                bucket["line_kinds"][k] += row.get("line_kinds", {}).get(k, 0)
            bucket["needs_review"] += int(row.get("status") == "needs_review")
            bucket["repaired"] += int(row.get("attempts", 1) > 1)
    return buckets


def _pct(correct: int, total: int) -> str:
    return f"{round(100 * correct / total)}%" if total else "–"


def render_markdown(summary: dict[str, dict[str, Any]], meta: dict[str, Any]) -> str:
    """Aggregate numbers only: no file names, vendors, or amounts, so it is safe to commit."""
    columns = [("all", "All"), ("plain", "Plain"), ("tricky", "Tricky")]
    counts = " | ".join(f"{label} (n={summary[key]['count']})" for key, label in columns)
    lines = [
        f"# Extraction eval: {meta.get('date', '')}",
        "",
        f"- Prompt version: `{meta.get('prompt_version', '')}`",
        f"- Model: `{meta.get('model', '')}`",
        "",
        f"| Field | {counts} |",
        "|---|" + "---|" * len(columns),
    ]
    for name, label in SCALAR_FIELDS:
        cells = " | ".join(
            _pct(summary[key]["fields"][name]["correct"], summary[key]["fields"][name]["total"])
            for key, _ in columns
        )
        lines.append(f"| {label} | {cells} |")

    def deadline_cell(key: str, metric: str) -> str:
        d = summary[key]["deadlines"]
        denominator = d["tp"] + (d["fp"] if metric == "precision" else d["fn"])
        return _pct(d["tp"], denominator)

    lines.append(
        "| Deadline precision | "
        + " | ".join(deadline_cell(k, "precision") for k, _ in columns)
        + " |"
    )
    lines.append(
        "| Deadline recall | " + " | ".join(deadline_cell(k, "recall") for k, _ in columns) + " |"
    )
    lines.append(
        "| Line-kind accuracy | "
        + " | ".join(
            _pct(summary[k]["line_kinds"]["correct"], summary[k]["line_kinds"]["total"])
            for k, _ in columns
        )
        + " |"
    )
    lines.append(
        "| Sent to review | "
        + " | ".join(_pct(summary[k]["needs_review"], summary[k]["count"]) for k, _ in columns)
        + " |"
    )
    lines.append(
        "| Needed a repair pass | "
        + " | ".join(_pct(summary[k]["repaired"], summary[k]["count"]) for k, _ in columns)
        + " |"
    )
    return "\n".join(lines) + "\n"
