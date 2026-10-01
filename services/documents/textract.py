"""Textract AnalyzeExpense: call it, and boil its nested response down to what extraction needs.

Textract *reads* well: summary fields (TOTAL, TAX, …), line-item rows, and raw lines. Its row
grouping is imperfect (summary lines often end up as "items"), so this module keeps all three
views side by side for the model, and leaves deciding what each line *is* to later steps.
"""

from dataclasses import dataclass, field
from typing import Any

from shared.money import format_cents, to_cents

MONEY_FIELDS = frozenset(
    {
        "TOTAL",
        "SUBTOTAL",
        "TAX",
        "AMOUNT_PAID",
        "AMOUNT_DUE",
        "GRATUITY",
        "DISCOUNT",
        "SERVICE_CHARGE",
        "PRIOR_BALANCE",
    }
)
HINT_FIELDS = frozenset({"VENDOR_NAME", "NAME", "INVOICE_RECEIPT_DATE", "DUE_DATE"})
LOW_CONFIDENCE = 80.0


@dataclass(frozen=True)
class SummaryField:
    type: str
    label: str | None
    text: str
    confidence: float
    cents: int | None


@dataclass(frozen=True)
class LineItemRow:
    row: int
    description: str
    price_cents: int | None
    quantity: str | None
    row_text: str | None
    confidence: float


@dataclass
class ExpenseResult:
    pages: int
    summary_fields: list[SummaryField] = field(default_factory=list)
    line_items: list[LineItemRow] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    currency: str | None = None

    @property
    def summary_money(self) -> dict[str, int]:
        """Best (highest-confidence) parsed amount per money field type."""
        best: dict[str, SummaryField] = {}
        for f in self.summary_fields:
            if f.type in MONEY_FIELDS and f.cents is not None:
                if f.type not in best or f.confidence > best[f.type].confidence:
                    best[f.type] = f
        return {t: f.cents for t, f in best.items() if f.cents is not None}

    @property
    def total_confidence(self) -> float | None:
        totals = [
            f.confidence for f in self.summary_fields if f.type == "TOTAL" and f.cents is not None
        ]
        return max(totals) if totals else None

    @property
    def low_confidence_fields(self) -> list[str]:
        watched = MONEY_FIELDS | HINT_FIELDS
        return sorted(
            {
                f.type
                for f in self.summary_fields
                if f.type in watched and f.confidence < LOW_CONFIDENCE
            }
        )

    @property
    def vendor(self) -> str | None:
        return self._first_text("VENDOR_NAME") or self._first_text("NAME")

    @property
    def date_text(self) -> str | None:
        return self._first_text("INVOICE_RECEIPT_DATE")

    def _first_text(self, type_: str) -> str | None:
        return next((f.text for f in self.summary_fields if f.type == type_ and f.text), None)

    def render_for_model(self) -> str:
        """Three labelled views of the same document, so the model can cross-check them."""
        summary = []
        for f in self.summary_fields:
            value = (
                format_cents(f.cents) if f.type in MONEY_FIELDS and f.cents is not None else f.text
            )
            label = f' (printed label "{f.label}")' if f.label else ""
            summary.append(f"{f.type}: {value}{label}")
        items = []
        for r in self.line_items:
            parts = [f"row {r.row}: {r.description}"]
            if r.price_cents is not None:
                parts.append(format_cents(r.price_cents))
            if r.quantity:
                parts.append(f"qty {r.quantity}")
            items.append(" | ".join(parts))
        return "\n".join(
            [
                "<textract_summary_fields>",
                *summary,
                "</textract_summary_fields>",
                "<textract_line_items>",
                *items,
                "</textract_line_items>",
                "<ocr_lines>",
                *self.lines,
                "</ocr_lines>",
            ]
        )


def parse_expense(responses: list[dict[str, Any]]) -> ExpenseResult:
    """Merge one sync response, or all paginated async responses, into one result."""
    pages = responses[0].get("DocumentMetadata", {}).get("Pages", 1) if responses else 1
    result = ExpenseResult(pages=pages)
    line_blocks: list[dict[str, Any]] = []
    row = 0
    for response in responses:
        for doc in response.get("ExpenseDocuments", []):
            for raw in doc.get("SummaryFields", []):
                parsed = _summary_field(raw)
                if parsed:
                    result.summary_fields.append(parsed)
                result.currency = result.currency or raw.get("Currency", {}).get("Code")
            for group in doc.get("LineItemGroups", []):
                for item in group.get("LineItems", []):
                    row += 1
                    result.line_items.append(_line_item(row, item))
            line_blocks.extend(b for b in doc.get("Blocks", []) if b.get("BlockType") == "LINE")
    line_blocks.sort(key=_reading_order)
    result.lines = [b["Text"] for b in line_blocks if b.get("Text")]
    return result


def _summary_field(raw: dict[str, Any]) -> SummaryField | None:
    value = raw.get("ValueDetection") or {}
    text = (value.get("Text") or "").strip()
    if not text:
        return None
    type_ = (raw.get("Type") or {}).get("Text", "OTHER")
    label = ((raw.get("LabelDetection") or {}).get("Text") or "").strip() or None
    cents = to_cents(text) if type_ in MONEY_FIELDS else None
    return SummaryField(type_, label, text, float(value.get("Confidence", 0.0)), cents)


def _line_item(row: int, item: dict[str, Any]) -> LineItemRow:
    fields: dict[str, dict[str, Any]] = {}
    for f in item.get("LineItemExpenseFields", []):
        fields.setdefault((f.get("Type") or {}).get("Text", ""), f.get("ValueDetection") or {})

    def text(type_: str) -> str | None:
        value = (fields.get(type_, {}).get("Text") or "").strip()
        return value or None

    confidences = [fields[t].get("Confidence", 0.0) for t in ("ITEM", "PRICE") if t in fields]
    return LineItemRow(
        row=row,
        description=text("ITEM") or text("EXPENSE_ROW") or "",
        price_cents=to_cents(text("PRICE")),
        quantity=text("QUANTITY"),
        row_text=text("EXPENSE_ROW"),
        confidence=float(min(confidences)) if confidences else 0.0,
    )


def _reading_order(block: dict[str, Any]) -> tuple[int, float, float]:
    box = (block.get("Geometry") or {}).get("BoundingBox") or {}
    return (block.get("Page", 1), round(box.get("Top", 0.0), 3), box.get("Left", 0.0))


# --- API calls ----------------------------------------------------------------------------------


def analyze_sync(client: Any, bucket: str, key: str) -> list[dict[str, Any]]:
    """Images and single-page documents: the result comes back in the same call."""
    return [client.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})]


def start_async(
    client: Any, bucket: str, key: str, doc_id: str, topic_arn: str, role_arn: str
) -> str:
    """PDFs: Textract works in the background and announces completion on the SNS topic.

    ClientRequestToken makes a retried start return the same job instead of paying twice.
    """
    response = client.start_expense_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        JobTag=doc_id,
        NotificationChannel={"SNSTopicArn": topic_arn, "RoleArn": role_arn},
        ClientRequestToken=doc_id,
    )
    return response["JobId"]


def fetch_async(client: Any, job_id: str) -> list[dict[str, Any]]:
    """All result pages of a finished job (results are paginated with NextToken)."""
    pages: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs = {"JobId": job_id} | ({"NextToken": token} if token else {})
        response = client.get_expense_analysis(**kwargs)
        status = response.get("JobStatus")
        if status != "SUCCEEDED":
            raise RuntimeError(
                f"Textract job {status}: {response.get('StatusMessage', 'no details')}"
            )
        pages.append(response)
        token = response.get("NextToken")
        if not token:
            return pages
