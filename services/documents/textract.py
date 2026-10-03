"""Textract AnalyzeExpense: call it, and boil its nested response down to what extraction needs.

Textract *reads* well: summary fields (TOTAL, TAX, …), line-item rows, and raw lines. Its row
grouping is imperfect (summary lines often end up as "items"), so this module keeps all three
views side by side for the model, and leaves deciding what each line *is* to later steps.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from documents.image_tiles import Box
from shared.money import format_cents, to_cents
from shared.pii import address_lines, sensitive_spans

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
# Textract fields that hold the customer's name or address: never shown to the model, and lines
# matching them are masked (phase 2c). The generic NAME/ADDRESS/STREET/CITY fields aren't here:
# on a paper receipt they hold the store's name and address.
CUSTOMER_FIELDS = frozenset({"RECEIVER_NAME", "RECEIVER_ADDRESS"})
MASKED_LINE = "[PERSONAL INFO]"
LOW_CONFIDENCE = 80.0


def _fenced(text: str) -> str:
    """Document text goes inside <document>/<ocr_lines> tags: swap angle brackets for look-alikes
    so a printed "</document>" can't close the untrusted-data block early."""
    return text.replace("<", "‹").replace(">", "›")


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
    # Where each of `lines` sits on page 1 (same order), for cutting image tiles.
    line_boxes: list[Box] = field(default_factory=list)
    # Textract's OCR confidence (0–100) for each of `lines`, same order (phase 2d).
    line_confidences: list[float] = field(default_factory=list)

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

    def address_lines(self) -> set[int]:
        """Lines holding the customer's name or address (masked whole, in text and image)."""
        return address_lines(
            self.lines,
            self.line_boxes if len(self.line_boxes) == len(self.lines) else None,
            [f.text for f in self.summary_fields if f.type in CUSTOMER_FIELDS],
        )

    def private_spans(self) -> list[tuple[int, float, float]]:
        """What to black out in an image: (line index, start, end) as fractions of the line's
        width. Address lines go whole; a card, account number, or email only its own characters,
        so an item line with a product code keeps its name and price."""
        whole = self.address_lines()
        # Numbers Textract places inside an item row are product codes (UPCs), not accounts.
        rows = " ".join(f"{r.row_text or ''} {r.description}" for r in self.line_items)
        spans = [(i, 0.0, 1.0) for i in sorted(whole)]
        for i, line in enumerate(self.lines):
            if i in whole:
                continue
            for s, e in sensitive_spans(line):
                if not _product_code(line, s, e, rows):
                    spans.append((i, s / len(line), e / len(line)))
        return spans

    def render_for_model(self) -> str:
        """Three labelled views of the same document, so the model can cross-check them.
        Customer names and addresses are left out; the caller still runs `redact` on the result."""
        hidden = self.address_lines()
        hidden_text = [self.lines[i].strip().lower() for i in hidden]
        summary = []
        for f in self.summary_fields:
            if f.type in CUSTOMER_FIELDS or (
                f.text.strip().lower() in hidden_text and f.type not in MONEY_FIELDS
            ):
                continue
            value = (
                format_cents(f.cents) if f.type in MONEY_FIELDS and f.cents is not None else f.text
            )
            label = f' (printed label "{f.label}")' if f.label else ""
            summary.append(_fenced(f"{f.type}: {value}{label}"))
        items = []
        for r in self.line_items:
            parts = [f"row {r.row}: {r.description}"]
            if r.price_cents is not None:
                parts.append(format_cents(r.price_cents))
            if r.quantity:
                parts.append(f"qty {r.quantity}")
            items.append(_fenced(" | ".join(parts)))
        return "\n".join(
            [
                "<textract_summary_fields>",
                *summary,
                "</textract_summary_fields>",
                "<textract_line_items>",
                *items,
                "</textract_line_items>",
                "<ocr_lines>",
                *(
                    MASKED_LINE if i in hidden else _fenced(line)
                    for i, line in enumerate(self.lines)
                ),
                "</ocr_lines>",
            ]
        )


# A reference line's number is kept private even when it's UPC-length.
_REFERENCE = re.compile(r"(?i)\b(?:ref|trans|tc|auth|approval|acct|account|member|card|id)\b|#")
_ITEM_NAME = re.compile(r"[A-Za-z]{2,}")


def _product_code(line: str, start: int, end: int, rows: str) -> bool:
    """A product code (UPC/EAN), not an account: Textract put it in an item row, or it's 12–13
    digits after an item name on a line that isn't a reference line."""
    groups = line[start:end].split()
    if not all(g.isdigit() for g in groups):
        return False
    if "".join(groups) in rows.replace(" ", ""):
        return True
    # "PAL ORI 828 006200000123": a size, then the code; the card rule reads them as one number.
    before = line[:start]
    return (
        len(groups[-1]) in (12, 13)
        and bool(_ITEM_NAME.search(before))
        and not _REFERENCE.search(before)
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
    line_blocks = [b for b in line_blocks if b.get("Text")]
    result.lines = [b["Text"] for b in line_blocks]
    result.line_confidences = [float(b.get("Confidence", 0.0)) for b in line_blocks]
    result.line_boxes = [_box(b) for b in line_blocks if b.get("Page", 1) == 1]
    return result


def _box(block: dict[str, Any]) -> Box:
    box = (block.get("Geometry") or {}).get("BoundingBox") or {}
    return Box(
        float(box.get("Left", 0.0)),
        float(box.get("Top", 0.0)),
        float(box.get("Width", 0.0)),
        float(box.get("Height", 0.0)),
        block["Text"],
    )


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


def _s3_object(bucket: str, key: str, version: str | None) -> dict[str, str]:
    """Pinning the version means OCR reads exactly the upload that was claimed."""
    obj = {"Bucket": bucket, "Name": key}
    return obj | {"Version": version} if version else obj


def analyze_sync(
    client: Any, bucket: str, key: str, version: str | None = None
) -> list[dict[str, Any]]:
    """Images and single-page documents: the result comes back in the same call."""
    return [client.analyze_expense(Document={"S3Object": _s3_object(bucket, key, version)})]


def start_async(
    client: Any,
    bucket: str,
    key: str,
    doc_id: str,
    topic_arn: str,
    role_arn: str,
    version: str | None = None,
) -> str:
    """PDFs: Textract works in the background and announces completion on the SNS topic.

    ClientRequestToken makes a retried start return the same job instead of paying twice.
    """
    response = client.start_expense_analysis(
        DocumentLocation={"S3Object": _s3_object(bucket, key, version)},
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
