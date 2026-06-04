"""Map cache for persisting and validating UIContextMaps on disk."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from testagent.exploration.ui_context_map import ElementInfo, UIContextMap


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets. Returns 0.0 when both are empty."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union


def _element_key(el: ElementInfo) -> str:
    """Create a comparable key for an element."""
    return f"{el.text}|{el.resource_id}"


def _safe_filename(value: str) -> str:
    """Replace non-alphanumeric characters (except dot, dash, underscore) with underscore."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value)


class MapCache:
    """Persists UIContextMap to JSON files and validates freshness."""

    def __init__(self, cache_dir: str = ".", max_age_days: int = 7) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_age_days = max_age_days

    def _cache_path(self, app_package: str, app_version: str) -> Path:
        safe_pkg = _safe_filename(app_package)
        safe_ver = _safe_filename(app_version)
        return self.cache_dir / f"{safe_pkg}_{safe_ver}.json"

    def save(self, app_package: str, app_version: str, context_map: UIContextMap) -> None:
        """Write context map to disk."""
        payload = {
            "app_package": app_package,
            "app_version": app_version,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "context_map": context_map.to_dict(),
        }
        path = self._cache_path(app_package, app_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, app_package: str, app_version: str) -> Optional[UIContextMap]:
        """Load cached map if it exists, version matches, and is not expired."""
        path = self._cache_path(app_package, app_version)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("app_version") != app_version:
            return None

        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now(timezone.utc) - saved_at > timedelta(days=self.max_age_days):
            return None

        return UIContextMap.from_dict(data["context_map"])

    def validate(
        self,
        app_package: str,
        app_version: str,
        current_elements: list[ElementInfo],
        threshold: float = 0.7,
    ) -> bool:
        """Return True if cached map's elements are similar enough to current elements."""
        cached = self.load(app_package, app_version)
        if cached is None:
            return False

        cached_keys: set[str] = set()
        for page in cached.pages:
            for el in page.elements:
                cached_keys.add(_element_key(el))

        current_keys = {_element_key(el) for el in current_elements}
        return _jaccard_similarity(cached_keys, current_keys) >= threshold
