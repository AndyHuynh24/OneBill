"""A free check before the model: skip emails with no sign of a subscription."""

import pytest

from subscriptions.signals import looks_like_subscription


@pytest.mark.parametrize(
    ("subject", "text"),
    [
        ("Your Netflix receipt", "Amount $15.49. Next billing date November 1, 2026."),
        ("Your subscription renews soon", "Spotify Premium will renew for €10.99 on Nov 1."),
        ("Your free trial ends in 3 days", "After your trial, Max costs $16.99/month."),
        ("Receipt from Apple", "iCloud+ 200 GB  $2.99  Monthly  Renews Nov 1, 2026"),
        ("Payment confirmation", "Thanks! Your gym membership payment of £30.00 was received."),
        ("Invoice", "ChatGPT Plus — USD 20.00 — billing period Oct 1 – Oct 31"),
    ],
)
def test_subscription_emails_pass(subject, text):
    assert looks_like_subscription(subject, text)


@pytest.mark.parametrize(
    ("subject", "text"),
    [
        ("Your Amazon order has shipped", "Order #123 total $45.20. Arriving Thursday."),
        ("50% off this weekend!", "Upgrade your plan and save big. Shop now."),  # no price
        ("Your receipt", "Coffee $4.50. Thanks for visiting!"),  # a price, no recurring words
        (
            "Weekly newsletter",
            "Here's what's new this month in our subscription box blog.",
        ),  # words, no price
        ("", ""),
    ],
)
def test_obvious_non_subscriptions_are_skipped(subject, text):
    assert not looks_like_subscription(subject, text)
