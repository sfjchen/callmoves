"""Checks that the library's theme regexes tag the phrases they are meant to and no longer overlap."""
import re

import pytest

from callmoves.run import THEMES


@pytest.mark.parametrize("text, want", [
    ("net interest margin expanded 4 bps", {"NII"}),
    ("gross margin of 46%", {"Margins"}),
    ("our investment banking deal pipeline is strong", {"Deals"}),
    ("FDA approval for the new indication", {"Pipeline"}),
    ("we raise full-year guidance", {"Guidance"}),
    ("data center demand", {"AI", "Demand"}),
])
def test_themes_tag_each_phrase_with_only_its_topics(text, want):
    assert {t for t, rx in THEMES.items() if re.search(rx, text, re.IGNORECASE)} == want
