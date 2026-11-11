"""
Tests the label statistics: Cohen's kappa on known tables and the headline-quote match behind the gold tier and the
clock check.
"""
import pytest

from callmoves.labels import agree, kappa, quotes


def test_kappa_matches_hand_computed_values():
    assert kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    # po = 0.5, pe = 0.5, so agreement is exactly chance
    assert kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)
    # po = 0.6, pe = (3*3 + 2*2) / 25 = 0.52
    assert kappa(["a", "a", "a", "b", "b"], ["a", "a", "b", "b", "a"]) == pytest.approx((0.6 - 0.52) / 0.48)
    assert kappa([], []) is None
    assert kappa(["a", "a"], ["a", "a"]) is None


def test_quotes_needs_half_the_content_words():
    said = "We now expect full year net interest income to be slightly below 2 billion in markets."
    co = "Wells Fargo & Company"
    assert quotes("WELLS FARGO CEO: EXPECT MARKETS NET INTEREST INCOME SLIGHTLY BELOW $2 BILLION", said, co)
    assert not quotes("WELLS FARGO ANNOUNCES NEW BUYBACK AUTHORIZATION OF $40 BILLION", said, co)
    assert not quotes("WFC: EXPECT", said, co)   # too few content words to tell
    # the company's name alone does not make a match
    assert not quotes("WELLS FARGO CFO: NIM EXPECTED TO STABILIZE IN Q4",
                      "Welcome and thank you for joining the Wells Fargo second quarter call.", co)


def test_agree_allows_adjacent_sentences_of_1_remark():
    st = {"choice": "statement", "t_ms": 600_000}
    assert agree(st, {"choice": "statement", "t_ms": 620_000})
    assert not agree(st, {"choice": "statement", "t_ms": 640_000})
    assert not agree(st, {"choice": "release", "t_ms": None})
    assert agree({"choice": "market", "t_ms": None}, {"choice": "market", "t_ms": None})
