# SPDX-License-Identifier: Apache-2.0
"""Blocked title words: folded, whole words, plurals."""
import pytest

from media_archivist.blockedwords import blocked, clean_words, fold


@pytest.mark.parametrize("title", ["Behind the scenes of X", "Behind-The-Scenes", "BEHIND_THE_SCENES",
                                   "behind   the\tscenes", "X (behind_the-scenes)"])
def test_separators_and_case_are_folded(title):
    assert blocked(title, ["behind the scenes"]) == "behind the scenes"


def test_punctuation_inside_a_phrase_matches():
    assert blocked("Making-Of Dark Water", ["making of"]) == "making of"
    assert blocked("Short Q&A with the director", ["q&a"]) == "q&a"


@pytest.mark.parametrize("title,word", [("Shadow", "ad"), ("Debts", "bts"), ("Reviewer", "review"),
                                        ("Clipper", "clip"), ("Trailerpark", "trailer"),
                                        ("Trailblazer", "trailer"), ("Podcaster", "podcast")])
def test_a_word_never_matches_inside_another(title, word):
    assert blocked(title, [word]) is None


def test_plurals_match():
    assert blocked("Official Trailers", ["trailer"]) == "trailer"
    assert blocked("Two Teasers", ["teaser"]) == "teaser"
    assert blocked("All the Clips", ["clip"]) == "clip"
    assert blocked("Watch these matches", ["match"]) == "match"
    assert blocked("An ad for it", ["ad"]) == "ad"
    assert blocked("BTS: day one", ["bts"]) == "bts"


def test_case_folding_and_nfkc_but_accents_are_kept():
    assert blocked("CAFÉ Scene", ["café"]) == "café"
    assert blocked("Die STRASSE", ["straße"]) == "straße"
    assert blocked("Ｔｒａｉｌｅｒ (full width)", ["trailer"]) == "trailer"
    assert blocked("Cafe Scene", ["café"]) is None
    assert blocked("résumé", ["re"]) is None


def test_the_first_listed_word_found_is_returned_as_listed():
    assert blocked("Trailer and teaser", ["Teaser", "TRAILER"]) == "Teaser"


def test_nothing_is_blocked_without_words():
    assert blocked("Trailer", []) is None
    assert blocked("Trailer", None) is None
    assert blocked("Trailer", [" ", ""]) is None
    assert blocked(None, ["trailer"]) is None


def test_clean_words():
    assert clean_words([" Trailer ", "trailer", "Behind-the-scenes", "behind the  scenes"]) == \
        ["Trailer", "Behind-the-scenes"]
    for bad in ("trailer", ["ok", ""], [1], None):
        with pytest.raises(ValueError):
            clean_words(bad)
    assert fold("  A_B-c  D ") == "a b c d"
