"""Email HTML → plain text for the model: keep what a person reads, drop what they don't."""

from subscriptions.html_text import MAX_CHARS, html_to_text

NETFLIX = """<!DOCTYPE html><html><head><title>Your receipt</title>
<style>.btn{color:red}</style><script>track()</script></head>
<body>
<div style="display:none;max-height:0">Your membership renews soon &#8199;&zwnj;</div>
<table role="presentation"><tr><td><img src="https://cdn/logo.png" alt="Netflix"></td></tr></table>
<h1>Thanks for your payment</h1>
<p>Hi Jamie,<br>We charged your card for your <b>Standard</b> plan.</p>
<table>
  <tr><td>Amount</td><td>$15.49</td></tr>
  <tr><td>Billing period</td><td>Oct 1, 2026 &ndash; Oct 31, 2026</td></tr>
  <tr><td>Next billing date</td><td>November 1, 2026</td></tr>
</table>
<p><a href="https://netflix.com/account?utm_source=email&amp;t=abc123">Manage account</a></p>
</body></html>"""


def test_keeps_the_text_a_person_reads_in_reading_order():
    text = html_to_text(NETFLIX)

    assert "Thanks for your payment" in text
    assert "Hi Jamie,\nWe charged your card for your Standard plan." in text
    assert "Amount | $15.49" in text  # table rows stay on one line
    assert "Billing period | Oct 1, 2026 – Oct 31, 2026" in text  # entities decoded
    assert "Next billing date | November 1, 2026" in text
    assert "Manage account" in text


def test_drops_scripts_styles_hidden_preheaders_titles_and_urls():
    text = html_to_text(NETFLIX)

    for gone in ("track()", ".btn", "renews soon", "Your receipt", "utm_source", "https://"):
        assert gone not in text


def test_whitespace_is_tidy():
    text = html_to_text("<p>  a   b </p>\n\n\n<p>c</p><div><div></div></div><p>d</p>")

    assert text == "a b\n\nc\n\nd"


def test_long_emails_are_cut_to_a_budget():
    text = html_to_text("<p>" + "word " * 20_000 + "</p>")

    assert len(text) <= MAX_CHARS


def test_broken_html_never_raises():
    assert html_to_text("<table><tr><td>$9.99<td>Plan</p></b></div>") == "$9.99 | Plan"
    assert html_to_text("") == ""


def test_plain_text_bodies_lose_tracking_links_and_invisible_padding():
    from subscriptions.html_text import tidy_text

    plain = "Your Spotify Premium receipt​\n\nTotal: $11.99\nManage: <https://spotify.com/x?t=1>\n"

    assert tidy_text(plain) == "Your Spotify Premium receipt\n\nTotal: $11.99\nManage:"
