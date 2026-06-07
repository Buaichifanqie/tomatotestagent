from __future__ import annotations

import pytest
from testagent.rule_engine.smart_comparator import SmartComparator


class TestNumericMatcher:
    def test_integer_vs_float(self):
        comp = SmartComparator()
        result = comp.compare("100.0", 100)
        assert result.matched is True
        assert result.matcher_used == "NumericMatcher"

    def test_string_number_vs_int(self):
        comp = SmartComparator()
        result = comp.compare("42", 42)
        assert result.matched is True

    def test_float_precision(self):
        comp = SmartComparator()
        result = comp.compare("100.00", 100)
        assert result.matched is True

    def test_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("100", 200)
        assert result.matched is False

    def test_non_numeric_falls_through(self):
        comp = SmartComparator()
        result = comp.compare("hello", 100)
        # Should not match with NumericMatcher
        assert result.matcher_used != "NumericMatcher" or not result.matched


class TestCurrencyMatcher:
    def test_yuan_symbol(self):
        comp = SmartComparator()
        result = comp.compare("¥150.00", 150)
        assert result.matched is True
        assert result.matcher_used == "CurrencyMatcher"

    def test_dollar_symbol(self):
        comp = SmartComparator()
        result = comp.compare("$99.99", 99.99)
        assert result.matched is True

    def test_with_comma(self):
        comp = SmartComparator()
        result = comp.compare("¥1,234.56", 1234.56)
        assert result.matched is True

    def test_currency_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("¥100", 200)
        assert result.matched is False

    def test_both_strings(self):
        comp = SmartComparator()
        result = comp.compare("¥100.00", "100")
        assert result.matched is True


class TestFuzzyStringMatcher:
    def test_case_insensitive(self):
        comp = SmartComparator()
        result = comp.compare("Hello", "hello")
        assert result.matched is True
        assert result.matcher_used == "FuzzyStringMatcher"

    def test_trim_whitespace(self):
        comp = SmartComparator()
        result = comp.compare("  hello  ", "hello")
        assert result.matched is True

    def test_mismatch(self):
        comp = SmartComparator()
        result = comp.compare("hello", "world")
        assert result.matched is False


class TestDatetimeMatcher:
    def test_iso_date_vs_timestamp(self):
        comp = SmartComparator()
        # 2026-06-07 in ISO format
        result = comp.compare("2026-06-07", "2026-06-07")
        assert result.matched is True
        assert result.matcher_used == "DatetimeMatcher"

    def test_different_format_same_date(self):
        comp = SmartComparator()
        result = comp.compare("2026/06/07", "2026-06-07")
        assert result.matched is True


class TestExplicitTransforms:
    def test_strip_currency_transform(self):
        comp = SmartComparator()
        result = comp.compare("¥200.00", 200, transform="strip_currency")
        assert result.matched is True
        assert result.matcher_used == "transform:strip_currency"

    def test_divide_by_100_transform(self):
        comp = SmartComparator()
        result = comp.compare("¥100.00", 1, transform="divide_by_100")
        # ¥100.00 -> strip -> 100.00 -> divide by 100 -> 1.0
        assert result.matched is True

    def test_map_transform(self):
        comp = SmartComparator()
        mapping = {"1": "待发货", "2": "已发货", "3": "已完成"}
        result = comp.compare("已发货", 2, transform={"type": "map", "rules": mapping})
        assert result.matched is True

    def test_strict_mode_no_auto_match(self):
        comp = SmartComparator()
        # Without transform, strict mode should do exact comparison
        result = comp.compare("¥100", 100, compare_mode="strict")
        assert result.matched is False  # "¥100" != 100 strictly


class TestNoneHandling:
    def test_both_none(self):
        comp = SmartComparator()
        result = comp.compare(None, None)
        assert result.matched is True
        assert result.matcher_used == "NoneGuard"

    def test_one_none(self):
        comp = SmartComparator()
        result = comp.compare(None, 100)
        assert result.matched is False
        assert result.matcher_used == "NoneGuard"

    def test_ui_none_expected_value(self):
        comp = SmartComparator()
        result = comp.compare("hello", None)
        assert result.matched is False
        assert result.matcher_used == "NoneGuard"


class TestCommaNumbers:
    def test_comma_separated_number(self):
        comp = SmartComparator()
        result = comp.compare("1,234", 1234)
        assert result.matched is True
        assert result.matcher_used == "NumericMatcher"

    def test_large_comma_number(self):
        comp = SmartComparator()
        result = comp.compare("100,000", 100000)
        assert result.matched is True

    def test_comma_number_vs_float(self):
        comp = SmartComparator()
        result = comp.compare("1,234.56", 1234.56)
        assert result.matched is True


class TestCompareResult:
    def test_result_fields(self):
        comp = SmartComparator()
        result = comp.compare("42", 42)
        assert hasattr(result, "matched")
        assert hasattr(result, "ui_value")
        assert hasattr(result, "expected_value")
        assert hasattr(result, "matcher_used")
        assert hasattr(result, "confidence")
        assert hasattr(result, "message")
