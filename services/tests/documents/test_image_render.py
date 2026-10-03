import io

from PIL import Image

from documents.image_render import model_images
from documents.image_tiles import MAX_EDGE, MAX_PIXELS, Box
from documents.textract import ExpenseResult, LineItemRow


def photo(width: int, height: int, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def receipt(lines: list[tuple[str, Box]]) -> ExpenseResult:
    return ExpenseResult(pages=1, lines=[t for t, _ in lines], line_boxes=[b for _, b in lines])


ROWS = [
    (f"ITEM {i} 4.97", Box(0.1, 0.03 + 0.05 * i, 0.8, 0.03, f"ITEM {i} 4.97")) for i in range(18)
]


def test_a_readable_photo_is_sent_as_one_image():
    images = model_images(photo(800, 1000), receipt(ROWS))

    assert len(images) == 1
    assert opened(images[0]).size == (800, 1000)


def test_the_customers_address_and_card_are_blacked_out():
    card = Box(0.1, 0.5, 0.5, 0.05, "VISA Ending in 6262")
    street = Box(0.1, 0.6, 0.5, 0.05, "12 Elm St")
    city = Box(0.1, 0.7, 0.5, 0.05, "Ottawa ON K1S 5B7")
    lines = [
        ROWS[0],
        ("VISA Ending in 6262", card),
        ("12 Elm St", street),
        ("Ottawa ON K1S 5B7", city),
    ]

    (image,) = model_images(photo(1000, 1000), receipt(lines))

    pixels = opened(image)

    def at(box: Box, fraction: float) -> tuple[int, int, int]:
        x = int((box.left + box.width * fraction) * 1000)
        return pixels.getpixel((x, int((box.top + box.height / 2) * 1000)))

    for box in (street, city):
        assert max(at(box, 0.5)) < 40, box.text  # address lines go whole
    assert max(at(card, 0.9)) < 40  # the card digits
    assert min(at(card, 0.1)) > 215  # "VISA" stays
    assert min(pixels.getpixel((500, 300))) > 215  # elsewhere untouched


def test_an_account_number_is_blacked_out_but_the_rest_of_the_line_stays():
    text = "Account number 004412345678"
    line = Box(0.1, 0.5, 0.8, 0.05, text)

    (image,) = model_images(photo(1000, 1000), receipt([ROWS[0], (text, line)]))

    pixels = opened(image)
    y = int(0.525 * 1000)
    assert min(pixels.getpixel((int((0.1 + 0.8 * 0.1) * 1000), y))) > 215  # "Account"
    assert max(pixels.getpixel((int((0.1 + 0.8 * 0.8) * 1000), y))) < 40  # the number


def test_a_product_code_textract_lists_in_an_item_row_isnt_blacked_out():
    """Paper Walmart receipts print a UPC on each item line, often with the price on its own
    line: it's part of an item row, not an account number."""
    text = "GV MILK 007874235186"
    line = Box(0.1, 0.5, 0.6, 0.05, text)
    expense = receipt([ROWS[0], (text, line)])
    expense.line_items = [LineItemRow(1, "GV MILK", 348, None, "GV MILK 007874235186 3.48", 99.0)]

    (image,) = model_images(photo(1000, 1000), expense)

    assert min(opened(image).getpixel((int((0.1 + 0.6 * 0.8) * 1000), 525))) > 215


def test_small_print_in_a_tall_screenshot_is_sent_as_tiles_within_the_models_limits():
    rows = [
        (f"ITEM {i} 3.96", Box(0.1, 0.004 + 0.0035 * i, 0.8, 0.0028, f"ITEM {i} 3.96"))
        for i in range(280)
    ]

    images = model_images(photo(1152, 4096), receipt(rows))

    assert len(images) > 1
    for data in images:
        w, h = opened(data).size
        assert max(w, h) <= MAX_EDGE
        assert w * h <= MAX_PIXELS


def test_without_line_positions_no_image_is_sent():
    """PII can't be masked without knowing where lines are: text only."""
    expense = ExpenseResult(pages=1, lines=["12 Elm St", "Ottawa ON K1S 5B7"])

    assert model_images(photo(800, 1000), expense) == []


def test_an_unreadable_file_means_text_only():
    assert model_images(b"not an image", receipt(ROWS)) == []


def test_exif_rotation_is_applied_before_cropping():
    (image,) = model_images(photo(1000, 800, orientation=6), receipt(ROWS))  # rotated 90°

    assert opened(image).size == (800, 1000)
