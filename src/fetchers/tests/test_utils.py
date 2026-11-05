# fetchers/tests/test_utils.py
"""Tests for fetcher utility functions: parse_date and to_float."""

from datetime import datetime, timezone

import pytest

from fetchers.utils import parse_date, to_float


class TestParseDate:
    """Tests for parse_date() — date string parsing into aware UTC datetime."""

    def test_iso8601_with_z_suffix(self):
        result = parse_date("2024-03-15T12:00:00Z")
        assert result == datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_iso8601_with_offset(self):
        result = parse_date("2024-03-15T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_iso8601_date_only(self):
        result = parse_date("2024-03-15")
        assert result == datetime(2024, 3, 15, tzinfo=timezone.utc)

    def test_rfc2822_string(self):
        result = parse_date("Mon, 01 Jan 2024 00:00:00 +0000")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert parse_date("") is None

    def test_malformed_returns_none(self):
        assert parse_date("not-a-date") is None

    def test_result_is_timezone_aware(self):
        result = parse_date("2024-03-15T12:00:00Z")
        assert result.tzinfo is not None


class TestToFloat:
    """Tests for to_float() — safe float conversion."""

    def test_int_input(self):
        assert to_float(42) == 42.0

    def test_float_input(self):
        assert to_float(3.14) == pytest.approx(3.14)

    def test_numeric_string(self):
        assert to_float("100") == 100.0

    def test_none_returns_none(self):
        assert to_float(None) is None

    def test_non_numeric_string_returns_none(self):
        assert to_float("abc") is None

    def test_empty_string_returns_none(self):
        assert to_float("") is None
