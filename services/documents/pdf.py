"""Tiny PDF checks without a PDF library (no extra dependency in the Lambda package)."""

import re

# "/Type /Page" objects, but not the "/Type /Pages" tree node.
_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?![A-Za-z])")


def is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def count_pages(data: bytes) -> int:
    """Approximate page count, used to refuse very long PDFs before paying for OCR.

    PDFs that compress their object streams hide page objects; those count as one page here,
    and Textract's own page count is recorded after processing.
    """
    return max(1, len(_PAGE_OBJECT.findall(data)))
