"""Tests for map_cache module."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from testagent.exploration.map_cache import MapCache, _element_key, _jaccard_similarity
from testagent.exploration.ui_context_map import ElementInfo, PageInfo, UIContextMap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_element(text: str, resource_id: str = "") -> ElementInfo:
    return ElementInfo(text=text, element_type="button", center_x=100, center_y=200, resource_id=resource_id)


def _make_map(elements: list[ElementInfo]) -> UIContextMap:
    page = PageInfo(name="TestPage", elements=elements)
    return UIContextMap(pages=[page])


# ---------------------------------------------------------------------------
# _jaccard_similarity
# ---------------------------------------------------------------------------

class TestJaccardSimilarity:
    def test_jaccard_similarity(self):
        """Partial overlap: {a,b} vs {b,c} => 1/3."""
        assert _jaccard_similarity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_jaccard_similarity_identical(self):
        assert _jaccard_similarity({"a", "b"}, {"a", "b"}) == pytest.approx(1.0)

    def test_jaccard_similarity_empty(self):
        assert _jaccard_similarity(set(), set()) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _element_key
# ---------------------------------------------------------------------------

class TestElementKey:
    def test_basic(self):
        el = _make_element("Login", resource_id="btn_login")
        assert _element_key(el) == "Login|btn_login"

    def test_empty_resource_id(self):
        el = _make_element("Submit")
        assert _element_key(el) == "Submit|"


# ---------------------------------------------------------------------------
# MapCache
# ---------------------------------------------------------------------------

class TestMapCache:
    def test_save_and_load(self, tmp_path: Path):
        cache = MapCache(cache_dir=str(tmp_path))
        elements = [_make_element("A", "id_a"), _make_element("B", "id_b")]
        original = _make_map(elements)

        cache.save("com.example", "1.0.0", original)
        loaded = cache.load("com.example", "1.0.0")

        assert loaded is not None
        assert len(loaded.pages) == 1
        assert loaded.pages[0].name == "TestPage"
        assert len(loaded.pages[0].elements) == 2
        assert loaded.pages[0].elements[0].text == "A"

    def test_load_returns_none_when_no_cache(self, tmp_path: Path):
        cache = MapCache(cache_dir=str(tmp_path))
        assert cache.load("com.example", "1.0.0") is None

    def test_load_returns_none_on_version_mismatch(self, tmp_path: Path):
        cache = MapCache(cache_dir=str(tmp_path))
        original = _make_map([_make_element("X")])
        cache.save("com.example", "1.0.0", original)

        assert cache.load("com.example", "2.0.0") is None

    def test_validate_with_similar_elements(self, tmp_path: Path):
        """2/3 match => 66% < 70% threshold => False.
        3/3 match => 100% >= 70% threshold => True."""
        cache = MapCache(cache_dir=str(tmp_path))
        saved_elements = [_make_element("A", "id_a"), _make_element("B", "id_b"), _make_element("C", "id_c")]
        cache.save("com.example", "1.0.0", _make_map(saved_elements))

        # Only 2 of 3 match
        partial = [_make_element("A", "id_a"), _make_element("B", "id_b"), _make_element("D", "id_d")]
        assert cache.validate("com.example", "1.0.0", partial, threshold=0.7) is False

        # All 3 match
        full = [_make_element("A", "id_a"), _make_element("B", "id_b"), _make_element("C", "id_c")]
        assert cache.validate("com.example", "1.0.0", full, threshold=0.7) is True

    def test_validate_returns_false_when_no_cache(self, tmp_path: Path):
        cache = MapCache(cache_dir=str(tmp_path))
        assert cache.validate("com.example", "1.0.0", [_make_element("A")]) is False

    def test_validate_returns_false_on_expired(self, tmp_path: Path):
        cache = MapCache(cache_dir=str(tmp_path), max_age_days=7)
        elements = [_make_element("A", "id_a")]
        cache.save("com.example", "1.0.0", _make_map(elements))

        # Backdate saved_at to 8 days ago
        cache_file = tmp_path / "com.example_1.0.0.json"
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        data["saved_at"] = old_time
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        assert cache.validate("com.example", "1.0.0", elements, threshold=0.7) is False
