import zlib

from documents.pdf import count_pages, is_pdf


def fake_pdf(pages: int) -> bytes:
    objects = b"".join(
        b"%d 0 obj << /Type /Page /Parent 1 0 R >> endobj\n" % (i + 3) for i in range(pages)
    )
    return (
        b"%PDF-1.7\n1 0 obj << /Type /Pages /Count "
        + str(pages).encode()
        + b" >> endobj\n"
        + objects
    )


def compressed_pdf(pages: int, count: int | None = None) -> bytes:
    """Page objects packed into a FlateDecode object stream, as most modern PDF writers do."""
    inner = b"".join(b"<< /Type /Page /Parent 1 0 R >>\n" for _ in range(pages))
    if count is not None:
        inner += b"<< /Type /Pages /Count %d /Kids [] >>\n" % count
    stream = zlib.compress(inner)
    return (
        b"%PDF-1.7\n5 0 obj << /Type /ObjStm /N 3 /First 10 /Filter /FlateDecode /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream\nendobj\n%%EOF\n"
    )


def test_counts_page_objects_not_the_pages_tree():
    assert count_pages(fake_pdf(1)) == 1
    assert count_pages(fake_pdf(3)) == 3
    assert count_pages(fake_pdf(12)) == 12


def test_counts_pages_hidden_in_compressed_object_streams():
    """The bypass from the security review: compressed pages used to count as 1."""
    assert count_pages(compressed_pdf(1)) == 1
    assert count_pages(compressed_pdf(25)) == 25


def test_uses_the_pages_tree_count_when_it_is_larger():
    assert count_pages(compressed_pdf(2, count=300)) == 300


def test_unknown_layout_is_reported_as_unknown_not_one_page():
    assert count_pages(b"%PDF-1.7\n(nothing recognizable)") is None


def test_corrupt_compressed_stream_is_ignored_safely():
    data = b"%PDF-1.7\n1 0 obj << /Filter /FlateDecode >>\nstream\nnot zlib data\nendstream\n"
    assert count_pages(data) is None


def test_decompression_is_capped():
    """A tiny stream that inflates enormously (zip bomb) must not exhaust Lambda memory."""
    bomb = zlib.compress(b"0" * (60 * 1024 * 1024))
    data = b"%PDF-1.7\n1 0 obj << /Filter /FlateDecode >>\nstream\n" + bomb + b"\nendstream\n"
    assert count_pages(data) is None


def test_is_pdf_checks_the_magic_bytes():
    assert is_pdf(fake_pdf(1))
    assert not is_pdf(b"\xff\xd8\xff\xe0 JPEG data")
