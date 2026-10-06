from documents.image_tiles import (
    MAX_TILES,
    MIN_LINE_PX,
    Box,
    model_scale,
    needs_tiles,
    plan_tiles,
)

W, H = 1152, 2048  # the Walmart screenshot


def product_column(left: float, right: float, products: int = 14) -> list[Box]:
    """Order listings: a two-line name, a price, "Qty 1", then a full-width return line."""
    boxes, y, step = [], 0.01, 0.97 / products
    name_right = left + (right - left) * 0.7
    for _ in range(products):
        boxes += [
            Box(left, y, name_right - left, 0.007, "AXE Aluminum-Free"),
            Box(left, y + 0.009, name_right - left, 0.007, "Deodorant Body Spray"),
            Box(name_right + 0.01, y, right - name_right - 0.01, 0.007, "$3.96"),
            Box(left, y + 0.018, 0.03, 0.007, "Qty 1"),
            Box(left, y + 0.045, right - left, 0.007, "Return eligible until Fri, Sep 18"),
        ]
        y += step
    return boxes


THREE_COLUMNS = product_column(0.09, 0.31) + product_column(0.38, 0.61) + product_column(0.68, 0.89)


def px(box: Box, w: int = W, h: int = H) -> tuple[float, float, float, float]:
    return box.left * w, box.top * h, box.right * w, box.bottom * h


def holds(tile: tuple[int, int, int, int], box: Box) -> bool:
    x0, y0, x1, y1 = px(box)
    return tile[0] <= x0 and x1 <= tile[2] and tile[1] <= y0 and y1 <= tile[3]


def test_big_print_needs_no_tiles():
    receipt = [Box(0.1, 0.05 * i, 0.8, 0.03, "BANANAS 1.29") for i in range(18)]  # 61 px lines

    assert not needs_tiles(receipt, W, H)


def test_small_print_in_a_tall_screenshot_needs_tiles():
    assert needs_tiles(THREE_COLUMNS, W, H)  # 14 px lines would shrink to ~10 px


def test_no_text_needs_no_tiles():
    assert not needs_tiles([], W, H)


def test_every_line_is_in_exactly_one_tile():
    """Nothing lost, nothing counted twice."""
    tiles = plan_tiles(THREE_COLUMNS, W, H)

    assert tiles
    for box in THREE_COLUMNS:
        assert sum(holds(t, box) for t in tiles) == 1, box


def test_columns_are_tiled_separately_in_reading_order():
    tiles = plan_tiles(THREE_COLUMNS, W, H)

    assert 3 <= len(tiles) <= MAX_TILES
    assert len({t[0] for t in tiles}) == 3  # one stack of tiles per column
    assert tiles == sorted(tiles, key=lambda t: (t[0], t[1]))


def test_each_tile_is_sent_big_enough_to_read():
    for x0, y0, x1, y1 in plan_tiles(THREE_COLUMNS, W, H):
        scale = model_scale(x1 - x0, y1 - y0)
        assert 0.007 * H * scale >= MIN_LINE_PX - 0.01


def test_names_on_the_left_and_prices_on_the_right_stay_one_column():
    """A paper receipt: no line spans the gap between names and prices, but a column of
    prices alone isn't a column of its own."""
    rows = []
    for i in range(60):
        rows += [
            Box(0.05, 0.016 * i, 0.4, 0.006, "GV LARGE EGGS 12CT"),
            Box(0.85, 0.016 * i, 0.1, 0.006, "3.93"),
        ]

    tiles = plan_tiles(rows, W, H)

    assert tiles
    assert len({(t[0], t[2]) for t in tiles}) == 1
    assert all(sum(holds(t, b) for t in tiles) == 1 for b in rows)


def test_a_phone_photo_tiles_only_the_receipt_not_the_table_around_it():
    """12 MP photo, receipt in the middle third, 40 px print: tiles skip the blank margins."""
    w, h = 3024, 4032
    receipt = [Box(0.33, 0.05 + 0.011 * i, 0.33, 0.0099, "ITEM NAME 4.97") for i in range(80)]

    tiles = plan_tiles(receipt, w, h)

    assert tiles
    assert all(x0 > 0.3 * w and x1 < 0.7 * w for x0, _, x1, _ in tiles)


def test_too_many_tiles_means_no_tiles():
    tiny = [
        Box(0.01 * c, 0.002 * r, 0.006, 0.001, "word") for c in range(0, 100, 2) for r in range(400)
    ]

    assert plan_tiles(tiny, 4000, 12000) == []
