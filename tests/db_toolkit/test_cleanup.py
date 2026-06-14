from __future__ import annotations

import pytest

from testagent.db_toolkit.cleanup import CleanupTracker
from testagent.db_toolkit.models import SqlOpType


class TestCleanupTracker:
    def test_empty_tracker(self):
        tracker = CleanupTracker()
        assert tracker.get_records() == []
        assert tracker.get_cleanup_sql() == []

    def test_record_insert(self):
        tracker = CleanupTracker()
        tracker.record_insert("users", inserted_ids=[10, 11])
        records = tracker.get_records()
        assert len(records) == 1
        assert records[0].op_type == SqlOpType.INSERT
        assert records[0].table == "users"
        assert records[0].inserted_ids == [10, 11]

    def test_record_update(self):
        tracker = CleanupTracker()
        tracker.record_update(
            "users",
            where_clause="id = :id",
            where_params={"id": 5},
            original_values=[{"id": 5, "name": "alice"}],
        )
        records = tracker.get_records()
        assert len(records) == 1
        assert records[0].op_type == SqlOpType.UPDATE
        assert records[0].original_values == [{"id": 5, "name": "alice"}]

    def test_record_delete(self):
        tracker = CleanupTracker()
        tracker.record_delete("users", deleted_rows=[{"id": 3, "name": "bob"}])
        records = tracker.get_records()
        assert len(records) == 1
        assert records[0].op_type == SqlOpType.DELETE
        assert records[0].deleted_rows == [{"id": 3, "name": "bob"}]

    def test_cleanup_sql_insert(self):
        tracker = CleanupTracker()
        tracker.record_insert("users", inserted_ids=[10, 11])
        sqls = tracker.get_cleanup_sql()
        assert len(sqls) == 1
        assert "DELETE FROM users WHERE id IN" in sqls[0]["sql"]
        assert set(sqls[0]["params"].values()) == {10, 11}

    def test_cleanup_sql_update(self):
        tracker = CleanupTracker()
        tracker.record_update(
            "users",
            where_clause="id = :id",
            where_params={"id": 5},
            original_values=[{"id": 5, "name": "alice"}],
        )
        sqls = tracker.get_cleanup_sql()
        assert len(sqls) == 1
        assert "UPDATE users SET" in sqls[0]["sql"]
        assert "alice" in sqls[0]["sql"]

    def test_cleanup_sql_delete(self):
        tracker = CleanupTracker()
        tracker.record_delete("users", deleted_rows=[{"id": 3, "name": "bob"}])
        sqls = tracker.get_cleanup_sql()
        assert len(sqls) == 1
        assert "INSERT INTO users" in sqls[0]["sql"]

    def test_clear(self):
        tracker = CleanupTracker()
        tracker.record_insert("users", inserted_ids=[1])
        tracker.clear()
        assert tracker.get_records() == []

    def test_multiple_records_order(self):
        tracker = CleanupTracker()
        tracker.record_insert("t1", inserted_ids=[1])
        tracker.record_insert("t2", inserted_ids=[2])
        tracker.record_delete("t3", deleted_rows=[{"id": 3}])
        sqls = tracker.get_cleanup_sql()
        # Cleanup in reverse order: delete first, then inserts
        assert len(sqls) == 3
