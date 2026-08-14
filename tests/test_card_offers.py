"""SBI debit-card offers.

An offer is a different shape of fact from a rate. A stale rate is imprecise;
an expired offer is false, and acting on it means going to a shop for a
discount that will not apply. So the thing under test is mostly the calendar.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from capabilities_impl import card_offers


def _at(monkeypatch, day: date) -> None:
    monkeypatch.setattr(card_offers, "_today", lambda: day)


def test_the_catalog_matches_what_the_bank_publishes():
    body = card_offers.get_card_offers(include_inactive=True)
    merchants = {o["merchant"] for o in body["offers"]}

    assert body["count"] == 9
    assert {"BigBasket", "Cleartrip", "Flipkart Minutes", "Apollo 24|7"} <= merchants
    # Every quoted figure has to be traceable back to the page it came from.
    assert body["source_url"].startswith("https://sbi.bank.in/")
    assert body["page_last_updated"] == "2026-08-11"


def test_an_offer_that_has_ended_is_reported_as_ended(monkeypatch):
    """The failure that matters. Flipkart Minutes runs to 31 Aug 2026; the day
    after, it must not come back as available."""
    _at(monkeypatch, date(2026, 9, 1))

    [offer] = card_offers.get_card_offers("flipkart minutes", include_inactive=True)["offers"]
    assert offer["status"] == "expired"
    assert offer["active"] is False
    assert offer["ended_days_ago"] == 1

    # And it is gone from the default view, which is what a plain
    # "what offers are there" question sees.
    live = {o["id"] for o in card_offers.get_card_offers()["offers"]}
    assert "flipkart_minutes" not in live


def test_an_offer_that_has_not_started_is_not_available_yet(monkeypatch):
    """Reliance Digital's Onam window opens 21 Aug. On the 18th the answer is
    "from the 21st", not "yes" and not "no"."""
    _at(monkeypatch, date(2026, 8, 18))

    [offer] = card_offers.get_card_offers("reliance", include_inactive=True)["offers"]
    # The main Independence Day window has closed by the 18th...
    assert offer["status"] == "expired"
    # ...but its Onam window is still ahead, and says how far.
    [onam] = offer["additional_windows"]
    assert onam["status"] == "upcoming"
    assert onam["starts_in_days"] == 3


def test_a_second_window_can_make_an_offer_active_again(monkeypatch):
    """Kerala's Onam extension sits outside the main run. During it, the offer
    is live even though the first window closed."""
    _at(monkeypatch, date(2026, 8, 25))

    [offer] = card_offers.get_card_offers("reliance", include_inactive=True)["offers"]
    assert offer["active"] is True
    assert offer["status"] == "active"


def test_days_remaining_is_computed_not_stored(monkeypatch):
    """A stored count would be wrong the morning after it was written."""
    _at(monkeypatch, date(2026, 8, 15))
    [offer] = card_offers.get_card_offers("apollo", include_inactive=True)["offers"]
    assert offer["days_remaining"] == 51

    _at(monkeypatch, date(2026, 10, 4))
    [offer] = card_offers.get_card_offers("apollo", include_inactive=True)["offers"]
    assert offer["days_remaining"] == 1


def test_offers_with_no_published_period_are_kept_but_not_claimed_active():
    """The bank lists Cleartrip with no dates. Dropping it would answer "no
    offers on Cleartrip", which is wrong and undetectable; claiming it is
    active asserts something the page never said."""
    [offer] = card_offers.get_card_offers("cleartrip")["offers"]

    assert offer["dates_published"] is False
    assert offer["status"] == "no_period_published"
    assert offer["active"] is False
    assert offer["category_discounts"]["domestic_hotels_percent"] == 15


def test_a_slab_carries_the_sentence_that_states_it():
    """Measured: asked for the Reliance slabs, the model read the
    Rs 50,000-99,999 band (flat Rs 2,500) as Rs 5,000 -- the band above it.
    Each slab now ships the sentence, so quoting beats deriving."""
    [offer] = card_offers.get_card_offers("reliance", include_inactive=True)["offers"]
    reads = [s["reads_as"] for s in offer["slabs"]]

    assert "Spend Rs 50,000 to Rs 99,999: flat Rs 2,500 off" in reads
    assert "Spend Rs 1,00,000 to Rs 1,99,999: flat Rs 5,000 off" in reads
    assert all("reads_as" in s for s in offer["slabs"])


def test_merchant_search_reaches_inside_the_business_bundle():
    """"Is there a DHL offer" should find it, even though DHL is one line
    inside an offer titled "Business Debit Card Offers"."""
    found = card_offers.get_card_offers("dhl", include_inactive=True)["offers"]
    assert [o["id"] for o in found] == ["business_debit_card"]


@pytest.mark.parametrize("query, expected", [
    ("amazon", {"amazon_fresh", "visa_global_platinum_activation"}),
    ("big basket", {"big_basket"}),
    ("apollo", {"apollo_247"}),
])
def test_merchants_are_matched_the_way_people_type_them(query, expected):
    found = {o["id"] for o in card_offers.get_card_offers(query, include_inactive=True)["offers"]}
    assert found == expected


def test_category_filter():
    grocery = {o["id"] for o in card_offers.get_card_offers(category="grocery")["offers"]}
    assert grocery == {"big_basket", "amazon_fresh"}


def test_an_incomplete_offer_says_so_rather_than_filling_the_gap():
    """The Visa activation voucher's table gives a merchant list and nothing
    else -- no value, no qualifying spend. Recording a plausible amount would
    be inventing one."""
    [offer] = card_offers.get_card_offers("visa_global", include_inactive=True)["offers"]

    assert offer["incomplete"] is True
    assert "no voucher value" in offer["incomplete_reason"].lower()
    for absent in ("voucher_value", "max_discount", "min_order_value"):
        assert absent not in offer


def test_the_scrape_itself_goes_stale_separately_from_any_offer(monkeypatch):
    """Offers churn monthly, so a months-old scrape is missing new ones even
    if every offer in it is still inside its own dates."""
    _at(monkeypatch, date(2026, 8, 20))
    assert card_offers.get_card_offers()["stale"] is False

    _at(monkeypatch, date(2026, 8, 15) + timedelta(days=60))
    assert card_offers.get_card_offers()["stale"] is True
