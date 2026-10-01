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


def test_counts_page_objects_not_the_pages_tree():
    assert count_pages(fake_pdf(1)) == 1
    assert count_pages(fake_pdf(3)) == 3
    assert count_pages(fake_pdf(12)) == 12


def test_unknown_layout_counts_as_one_page():
    assert count_pages(b"%PDF-1.7\n(compressed object streams)") == 1


def test_is_pdf_checks_the_magic_bytes():
    assert is_pdf(fake_pdf(1))
    assert not is_pdf(b"\xff\xd8\xff\xe0 JPEG data")
