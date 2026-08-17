"""
Tests for services/newsletter/_domains.infer_domain.

The regression guarded here: keywords used to be matched as raw substrings, so
"rate" fired inside "corporate"/"separate"/"moderate" and "app" fired inside
"happened". General news was silently filed under Economy and Technology, which
also poisoned personalisation because the domain feeds the topic slug.
"""
import pytest

from services.newsletter._domains import DOMAIN_KEYWORDS, SECTION_ORDER, infer_domain


@pytest.mark.parametrize(
    "label",
    [
        "Corporate restructuring in Germany",     # "rate" inside "corporate"
        "Separate ceasefire talks resume",        # "rate" inside "separate"
        "What happened at the UN summit",         # "app"  inside "happened"
        "Accurate reporting from the region",     # "rate" inside "accurate"
        "Deliberate escalation on the border",    # "rate" inside "deliberate"
    ],
)
def test_substrings_inside_longer_words_do_not_match(label):
    """A keyword buried inside an unrelated word must not claim the story."""
    assert infer_domain(label) == "World"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Fed holds interest rate steady", "Economy"),
        ("Tariff talks stall between US and China", "Economy"),
        ("OpenAI releases new model", "AI"),
        ("AI safety summit convenes in Seoul", "AI"),
        ("Large language model breaks benchmark", "AI"),
        ("Apple unveils new chip", "Technology"),
        ("India cricket team wins series", "India"),
        ("Premier League title race tightens", "Sport"),
        ("Formula 1 season finale", "Sport"),
        ("New vaccine shows promise", "Science"),
        ("Box office record for summer film", "Entertainment"),
    ],
)
def test_genuine_keywords_still_match(label, expected):
    """Word-boundary matching must not break legitimate hits."""
    assert infer_domain(label) == expected


def test_bare_ai_matches_without_space_padding():
    """The old implementation needed ' ai ' padded; boundaries handle it now."""
    assert infer_domain("AI regulation debated") == "AI"
    assert infer_domain("Regulators weigh AI") == "AI"


def test_ai_does_not_match_inside_words():
    assert infer_domain("Air quality worsens in Lahore") == "World"
    assert infer_domain("Said aide resigns") == "World"


def test_matching_is_case_insensitive():
    assert infer_domain("TARIFF war escalates") == "Economy"
    assert infer_domain("tariff war escalates") == "Economy"


def test_unmatched_label_falls_through_to_world():
    assert infer_domain("Something entirely unremarkable") == "World"
    assert infer_domain("") == "World"


def test_every_keyword_domain_is_renderable():
    """
    assembler.py ranks sections via SECTION_ORDER; a domain that can be
    returned but is missing from SECTION_ORDER sorts to the end silently.
    """
    for domain in DOMAIN_KEYWORDS:
        assert domain in SECTION_ORDER, f"{domain} missing from SECTION_ORDER"
