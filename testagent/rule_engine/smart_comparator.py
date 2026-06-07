from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from testagent.rule_engine.models import CompareResult


class SmartComparator:
    """Intelligent comparison with three-layer fallback.

    Layer 1: Built-in Smart Matchers (80% of cases, zero config)
    Layer 2: Explicit Transforms (19% of cases)
    Layer 3: LLM Semantic (deferred to V1.1+)
    """

    def compare(
        self,
        ui_value: Any,
        expected_value: Any,
        transform: Any = None,
        compare_mode: str = "auto",
    ) -> CompareResult:
        """Compare two values using the funnel strategy.

        Args:
            ui_value: Value extracted from UI.
            expected_value: Value from API/DB.
            transform: Optional transform to apply before comparison.
            compare_mode: "auto" (funnel) or "strict" (exact match).

        Returns:
            CompareResult with match status and metadata.
        """
        # Layer 2: Explicit transform takes priority
        if transform is not None:
            return self._apply_transform(ui_value, expected_value, transform)

        # Strict mode: no auto-matching, direct comparison
        if compare_mode == "strict":
            matched = str(ui_value) == str(expected_value)
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="strict",
                confidence=1.0,
                message="Exact string match" if matched else f"'{ui_value}' != '{expected_value}'",
            )

        # Layer 1: Auto-match funnel
        return self._auto_match(ui_value, expected_value)

    def _auto_match(self, ui_value: Any, expected_value: Any) -> CompareResult:
        """Try built-in matchers in order."""
        matchers = [
            ("NumericMatcher", self._try_numeric),
            ("CurrencyMatcher", self._try_currency),
            ("DatetimeMatcher", self._try_datetime),
            ("FuzzyStringMatcher", self._try_fuzzy_string),
        ]

        for name, matcher_fn in matchers:
            result = matcher_fn(ui_value, expected_value)
            if result is not None:
                return result

        # Fallback: exact string comparison
        matched = str(ui_value) == str(expected_value)
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="exact_string",
            confidence=1.0 if matched else 0.0,
            message="Exact match" if matched else f"'{ui_value}' != '{expected_value}'",
        )

    def _try_numeric(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try numeric comparison."""
        try:
            ui_num = float(str(ui_value).strip())
            exp_num = float(str(expected_value).strip())
            matched = abs(ui_num - exp_num) < 1e-9
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="NumericMatcher",
                confidence=1.0,
                message=f"Numeric: {ui_num} {'==' if matched else '!='} {exp_num}",
            )
        except (ValueError, TypeError):
            return None

    def _try_currency(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try currency comparison (strip $ and commas)."""
        ui_str = str(ui_value).strip()
        exp_str = str(expected_value).strip()

        # Strip currency symbols and commas
        ui_clean = re.sub(r"[$,¥€£]", "", ui_str).strip()
        exp_clean = re.sub(r"[$,¥€£]", "", exp_str).strip()

        # Check if either had currency symbols
        has_currency = any(s in ui_str for s in "$¥€£") or any(
            s in exp_str for s in "$¥€£"
        )
        if not has_currency:
            return None

        try:
            ui_num = float(ui_clean)
            exp_num = float(exp_clean)
            matched = abs(ui_num - exp_num) < 1e-9
            return CompareResult(
                matched=matched,
                ui_value=ui_value,
                expected_value=expected_value,
                matcher_used="CurrencyMatcher",
                confidence=1.0,
                message=f"Currency: {ui_num} {'==' if matched else '!='} {exp_num}",
            )
        except (ValueError, TypeError):
            return None

    def _try_datetime(self, ui_value: Any, expected_value: Any) -> CompareResult | None:
        """Try datetime comparison (normalize formats)."""
        ui_str = str(ui_value).strip()
        exp_str = str(expected_value).strip()

        # Common date patterns
        date_patterns = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y%m%d",
        ]

        ui_dt = None
        exp_dt = None

        for fmt in date_patterns:
            try:
                ui_dt = datetime.strptime(ui_str, fmt)
                break
            except ValueError:
                continue

        for fmt in date_patterns:
            try:
                exp_dt = datetime.strptime(exp_str, fmt)
                break
            except ValueError:
                continue

        if ui_dt is None or exp_dt is None:
            return None

        matched = ui_dt == exp_dt
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="DatetimeMatcher",
            confidence=1.0,
            message=f"Datetime: {ui_dt} {'==' if matched else '!='} {exp_dt}",
        )

    def _try_fuzzy_string(
        self, ui_value: Any, expected_value: Any
    ) -> CompareResult | None:
        """Try fuzzy string comparison (case-insensitive, trim)."""
        ui_str = str(ui_value).strip().lower()
        exp_str = str(expected_value).strip().lower()

        # Only use fuzzy if both are non-numeric strings
        try:
            float(ui_str)
            float(exp_str)
            return None  # Both numeric, let NumericMatcher handle
        except ValueError:
            pass

        matched = ui_str == exp_str
        return CompareResult(
            matched=matched,
            ui_value=ui_value,
            expected_value=expected_value,
            matcher_used="FuzzyStringMatcher",
            confidence=1.0 if matched else 0.0,
            message=f"Fuzzy: '{ui_str}' {'==' if matched else '!='} '{exp_str}'",
        )

    def _apply_transform(
        self, ui_value: Any, expected_value: Any, transform: Any
    ) -> CompareResult:
        """Apply an explicit transform before comparison."""
        if isinstance(transform, str):
            # Built-in transform name
            transformed = self._run_builtin_transform(ui_value, transform)
            matcher_label = f"transform:{transform}"
            confidence = 1.0
        elif isinstance(transform, dict):
            # Complex transform with rules
            transform_type = transform.get("type", "")
            if transform_type == "map":
                transformed = self._run_map_transform(
                    ui_value, transform.get("rules", {})
                )
            else:
                transformed = ui_value
            matcher_label = f"transform:{transform_type}"
            confidence = 1.0
        else:
            transformed = ui_value
            matcher_label = "transform:custom"
            confidence = 1.0

        # Compare transformed value with expected
        try:
            matched = (
                abs(float(str(transformed)) - float(str(expected_value))) < 1e-9
            )
        except (ValueError, TypeError):
            matched = (
                str(transformed).strip().lower()
                == str(expected_value).strip().lower()
            )

        return CompareResult(
            matched=matched,
            ui_value=transformed,
            expected_value=expected_value,
            matcher_used=matcher_label,
            confidence=confidence,
            message=f"Transform applied: {ui_value} -> {transformed}, "
            f"{'matched' if matched else 'mismatch'}",
        )

    @staticmethod
    def _run_builtin_transform(value: Any, transform_name: str) -> Any:
        """Run a built-in transform by name."""
        value_str = str(value).strip()

        if transform_name == "strip_currency":
            return re.sub(r"[$,¥€£]", "", value_str).strip()
        elif transform_name == "divide_by_100":
            cleaned = re.sub(r"[$,¥€£]", "", value_str).strip()
            try:
                return float(cleaned) / 100
            except ValueError:
                return value
        else:
            return value

    @staticmethod
    def _run_map_transform(value: Any, rules: dict[str, str]) -> Any:
        """Run a mapping transform (e.g., status code to text)."""
        value_str = str(value).strip()
        # Try reverse mapping (value -> key)
        for key, mapped_value in rules.items():
            if value_str == mapped_value:
                return key
        # Try direct mapping (key -> value)
        return rules.get(value_str, value)
