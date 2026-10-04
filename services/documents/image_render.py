"""Turn an uploaded photo into the image(s) the model sees: PII blacked out, tiled if the print
is small, each within the model's size limits (design: phase2c-image-tiles).

Returns [] (text only) whenever that can't be done safely: no line positions to mask PII with,
an unreadable file, or too many tiles.
"""

import io
import math

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from documents.image_tiles import model_scale, needs_tiles, plan_tiles
from documents.textract import ExpenseResult

MAX_PIXELS = 50_000_000  # checked from the header, before decoding (a 48 MP phone photo is fine)
DRAFT_ABOVE = 24_000_000  # decode big JPEGs at half size: plenty for tiles, half the memory
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow's own guard, as a backstop
MASK_PAD_PX = 3
JPEG_QUALITY = 90


def model_images(data: bytes, expense: ExpenseResult) -> list[bytes]:
    boxes = expense.line_boxes
    if not boxes or len(boxes) != len(expense.lines):
        return []
    try:
        image = Image.open(io.BytesIO(data), formats=["JPEG", "PNG"])  # header only, so far
        if image.width * image.height > MAX_PIXELS:
            return []
        # A photo stored sideways with an EXIF "rotate me" tag: Textract measures its boxes on
        # the photo as viewed (checked 2026-10-04 with a real orientation-6 photo), so the
        # masks are drawn after applying the same rotation (exif_transpose below).
        if image.format == "JPEG" and image.width * image.height > DRAFT_ABOVE:
            image.draft("RGB", (image.width // 2, image.height // 2))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return []
    width, height = image.size

    draw = ImageDraw.Draw(image)
    for i, start, end in expense.private_spans():
        b = boxes[i]
        if b.tilted:
            start, end = 0.0, 1.0  # a slanted line: the proportional guess can miss, mask it all
        # Characters are placed proportionally along the line; pad by one character each side.
        char = b.width / max(1, len(expense.lines[i]))
        left = b.left + b.width * start - (char if start > 0 else 0)
        right = b.left + b.width * end + (char if end < 1 else 0)
        draw.rectangle(
            (
                math.floor(left * width) - MASK_PAD_PX,
                math.floor(b.top * height) - MASK_PAD_PX,
                math.ceil(right * width) + MASK_PAD_PX,
                math.ceil(b.bottom * height) + MASK_PAD_PX,
            ),
            fill="black",
        )

    if needs_tiles(boxes, width, height):
        tiles = plan_tiles(boxes, width, height)
        if not tiles:
            return []
    else:
        tiles = [(0, 0, width, height)]
    return [_jpeg(image.crop(tile)) for tile in tiles]


def _jpeg(tile: Image.Image) -> bytes:
    """Shrink to the model's limits here (it would anyway), so less is uploaded."""
    scale = model_scale(*tile.size)
    if scale < 1:
        size = (max(1, int(tile.width * scale)), max(1, int(tile.height * scale)))
        tile = tile.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    tile.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
