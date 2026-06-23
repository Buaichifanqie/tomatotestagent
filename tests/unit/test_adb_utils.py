"""Tests for testagent.common.adb_utils."""

from __future__ import annotations

from unittest.mock import patch

from testagent.common.adb_utils import adb_command


class TestAdbCommand:
    def test_prepends_s_udid(self) -> None:
        """adb_command should insert ``-s <udid>`` after ``adb``."""
        with patch("testagent.common.adb_utils.subprocess.run") as mock_run:
            adb_command("emulator-5554", "shell", "echo", "hello")
        args = mock_run.call_args[0][0]
        assert args == ["adb", "-s", "emulator-5554", "shell", "echo", "hello"]

    def test_empty_udid_fallback(self) -> None:
        """When ``udid`` is empty, omit the ``-s`` flag (single-device compat)."""
        with patch("testagent.common.adb_utils.subprocess.run") as mock_run:
            adb_command("", "logcat", "-c")
        args = mock_run.call_args[0][0]
        assert args == ["adb", "logcat", "-c"]

    def test_forwards_kwargs(self) -> None:
        with patch("testagent.common.adb_utils.subprocess.run") as mock_run:
            adb_command("dev1", "logcat", "-c", capture_output=True, timeout=5)
        kwargs = mock_run.call_args.kwargs
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 5
