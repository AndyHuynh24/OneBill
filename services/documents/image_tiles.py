"""Cut a photo into tiles the model can read at full sharpness (design: phase2c-image-tiles).

The model shrinks any image over MAX_EDGE px or MAX_PIXELS to fit, which blurs small print in a
tall screenshot. When the typical OCR line would come out smaller than MIN_LINE_PX, the photo is
cut into tiles: one stack per text column, cut at the blank gaps between lines (found from
Textract's line boxes), so no line is ever split and none appears in two tiles.

Planning is pure geometry and is tested on its own. Rendering, which needs Pillow, is separate.
"""

import math
import re
import statistics
from dataclasses import dataclass

# Anthropic vision docs: larger images are downscaled to fit these.
MAX_EDGE = 1568
MAX_PIXELS = 1_150_000
MIN_LINE_PX = 14  # below this, digits like 3/8 and 5/6 start to blur
MAX_TILES = 8
PAD_PX = 8
MIN_COLUMN_LINES = 5
_WORD = re.compile(r"[^\W\d_]{3,}")


@dataclass(frozen=True)
class Box:
    """A Textract LINE: position as fractions of the page (0–1), and its text."""

    left: float
    top: float
    width: float
    height: float
    text: str = ""

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


Tile = tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels


def model_scale(width: float, height: float) -> float:
    """How much the model shrinks an image of this size (1.0 = not at all)."""
    return min(1.0, MAX_EDGE / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))


def _median_line_px(boxes: list[Box], height: int) -> float:
    return statistics.median(b.height * height for b in boxes)


def needs_tiles(boxes: list[Box], width: int, height: int) -> bool:
    if not boxes or _median_line_px(boxes, height) <= 0:
        return False
    return _median_line_px(boxes, height) * model_scale(width, height) < MIN_LINE_PX


def plan_tiles(boxes: list[Box], width: int, height: int) -> list[Tile]:
    """Tiles in reading order (column by column, top to bottom), or [] if it would take more
    than MAX_TILES or a column is too wide to read even on its own."""
    if not boxes or _median_line_px(boxes, height) <= 0:
        return []
    # How much a tile may still be shrunk while its typical line stays readable.
    min_scale = min(1.0, MIN_LINE_PX / _median_line_px(boxes, height))
    tiles: list[Tile] = []
    for column in _columns(boxes, width):
        x0 = max(0, math.floor(min(b.left for b in column) * width) - PAD_PX)
        x1 = min(width, math.ceil(max(b.right for b in column) * width) + PAD_PX)
        col_width = x1 - x0
        if col_width * min_scale > MAX_EDGE:
            return []
        max_height = min(MAX_EDGE / min_scale, MAX_PIXELS / (min_scale**2 * col_width))
        for y0, y1 in _rows(column, height, int(max_height)):
            tiles.append((x0, y0, x1, y1))
            if len(tiles) > MAX_TILES:
                return []
    return tiles


def _columns(boxes: list[Box], width: int) -> list[list[Box]]:
    """Split at vertical gutters that no line crosses. A side with no words (a run of prices)
    isn't a column: it stays with its neighbour."""
    spans = sorted((b.left * width, b.right * width) for b in boxes)
    gutters, reach = [], spans[0][1]
    min_gap = max(8.0, 0.01 * width)
    for left, right in spans[1:]:
        if left - reach >= min_gap:
            gutters.append((reach + left) / 2)
        reach = max(reach, right)
    groups: list[list[Box]] = [[] for _ in range(len(gutters) + 1)]
    for box in boxes:
        groups[sum(box.left * width > g for g in gutters)].append(box)
    columns: list[list[Box]] = []
    for group in groups:
        if columns and not (_is_column(group) and _is_column(columns[-1])):
            columns[-1] += group
        else:
            columns.append(group)
    if len(columns) > 1 and not _is_column(columns[0]):
        columns[1] = columns[0] + columns[1]
        columns.pop(0)
    return columns


def _is_column(boxes: list[Box]) -> bool:
    worded = sum(1 for b in boxes if _WORD.search(b.text))
    return len(boxes) >= MIN_COLUMN_LINES and worded >= 0.2 * len(boxes)


def _rows(column: list[Box], height: int, max_height: int) -> list[tuple[int, int]]:
    """Cut a column at gaps between lines, preferring the widest gap in the last 40% of the
    allowed height (between products rather than inside one)."""
    spans = sorted((b.top * height, b.bottom * height) for b in column)
    gaps: list[tuple[float, float]] = []  # (cut y, gap size)
    reach = spans[0][1]
    for top, bottom in spans[1:]:
        if top > reach:
            gaps.append(((reach + top) / 2, top - reach))
        reach = max(reach, bottom)
    start = max(0, math.floor(spans[0][0]) - PAD_PX)
    end = min(height, math.ceil(reach) + PAD_PX)
    rows = []
    while end - start > max_height:
        limit = start + max_height
        window = [g for g in gaps if start + 0.6 * max_height <= g[0] <= limit]
        fallback = [g for g in gaps if start < g[0] <= limit]
        if window:
            cut = max(window, key=lambda g: g[1])[0]
        elif fallback:
            cut = fallback[-1][0]
        else:
            cut = limit  # one line taller than a tile: nothing better to do
        rows.append((start, round(cut)))
        start = round(cut)
    rows.append((start, end))
    return rows
