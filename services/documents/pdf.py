"""Tiny PDF checks with the standard library only (no extra dependency in the Lambda package).

Used to refuse long PDFs *before* paying for OCR. Most modern PDFs pack page objects into
compressed object streams, so streams are inflated (with a size cap) before counting.
If the page count can't be determined, callers should refuse the file rather than guess.
"""

import re
import zlib

# "/Type /Page" objects, but not the "/Type /Pages" tree node.
_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
# "/Count N" on a /Pages node: the total page count of that subtree.
_PAGES_COUNT = re.compile(
    rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)|/Count\s+(\d+)[^>]*?/Type\s*/Pages\b"
)
_FLATE_STREAM = re.compile(rb"/FlateDecode.*?stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
MAX_INFLATED_BYTES = 50 * 1024 * 1024  # guard against decompression bombs


def is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def count_pages(data: bytes) -> int | None:
    """Page count from page objects and /Pages counts, including compressed streams.

    Returns None when nothing recognizable is found (or decompression hits the cap).
    """
    chunks = [data]
    budget = MAX_INFLATED_BYTES
    for match in _FLATE_STREAM.finditer(data):
        inflater = zlib.decompressobj()
        try:
            inflated = inflater.decompress(match.group(1), budget)
        except zlib.error:
            continue  # not a valid stream; ignore it
        if inflater.unconsumed_tail:
            return None  # over the cap: refuse rather than guess
        budget -= len(inflated)
        chunks.append(inflated)

    page_objects = sum(len(_PAGE_OBJECT.findall(chunk)) for chunk in chunks)
    tree_counts = [int(a or b) for chunk in chunks for a, b in _PAGES_COUNT.findall(chunk)]
    best = max([page_objects, *tree_counts])
    return best or None
