"""
tests/unit/test_slugify.py
---------------------------
Unit tests for core.utils.slugify.
"""
import pytest
from core.utils import slugify


def test_basic_lowercase():
    assert slugify("US-China Trade Tariffs") == "us-china-trade-tariffs"


def test_special_characters_stripped():
    assert slugify("AI & Machine Learning!") == "ai-machine-learning"


def test_multiple_spaces_collapsed():
    assert slugify("The  Federal   Reserve") == "the-federal-reserve"


def test_already_slug():
    assert slugify("bitcoin-price") == "bitcoin-price"


def test_leading_trailing_hyphens_stripped():
    assert slugify("--leading and trailing--") == "leading-and-trailing"


def test_empty_string():
    assert slugify("") == ""


def test_numbers_preserved():
    assert slugify("COVID-19 Update") == "covid-19-update"


def test_unicode_normalised():
    # Non-word characters (accented) are stripped
    result = slugify("Nairobi's Economy")
    assert "nairobi" in result
    assert " " not in result
