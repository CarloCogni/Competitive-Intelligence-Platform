# core/tests/test_validators.py
"""Tests for core model validators."""

import pytest
from django.core.exceptions import ValidationError

from core.models import validate_keyword_list


class TestValidateKeywordList:
    """Tests for validate_keyword_list — JSONField validator on Entity keyword fields."""

    def test_valid_list(self):
        validate_keyword_list(["bitcoin", "wallet"])  # should not raise

    def test_empty_list_is_valid(self):
        validate_keyword_list([])  # should not raise

    def test_non_list_raises(self):
        with pytest.raises(ValidationError, match="list of strings"):
            validate_keyword_list("not-a-list")

    def test_too_many_keywords_raises(self):
        with pytest.raises(ValidationError, match="At most"):
            validate_keyword_list([f"kw{i}" for i in range(11)])

    def test_non_string_item_raises(self):
        with pytest.raises(ValidationError, match="must be a string"):
            validate_keyword_list([1, 2, 3])

    def test_too_long_keyword_raises(self):
        with pytest.raises(ValidationError, match="at most"):
            validate_keyword_list(["x" * 101])
