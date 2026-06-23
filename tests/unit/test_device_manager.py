"""Tests for testagent.plan.device_manager."""

from __future__ import annotations

from unittest.mock import patch

from testagent.plan.device_manager import DeviceManager, DeviceInfo


class TestDeviceManager:
    def test_discover_devices_parses_adb_output(self) -> None:
        fake_output = (
            "List of devices attached\n"
            "emulator-5554          device product:sdk_google_phone_x86 model:Pixel_6 device:generic_x86\n"
            "192.168.1.100:5555     device product:xiaomi model:Xiaomi_13 device:xiaomi13\n"
        )
        dm = DeviceManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.returncode = 0
            devices = dm.discover_devices()
        assert len(devices) == 2
        assert devices[0].udid == "emulator-5554"
        assert devices[0].name == "Pixel 6"
        assert devices[1].udid == "192.168.1.100:5555"
        assert devices[1].name == "Xiaomi 13"

    def test_discover_devices_empty_when_no_devices(self) -> None:
        fake_output = "List of devices attached\n\n"
        dm = DeviceManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.returncode = 0
            assert dm.discover_devices() == []

    def test_discover_devices_filters_offline(self) -> None:
        fake_output = (
            "List of devices attached\n"
            "emulator-5554          device\n"
            "emulator-5556          offline\n"
        )
        dm = DeviceManager()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.returncode = 0
            devices = dm.discover_devices()
        assert len(devices) == 1
        assert devices[0].udid == "emulator-5554"
