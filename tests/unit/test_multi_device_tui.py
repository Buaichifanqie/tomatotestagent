"""Tests for testagent.plan.multi_device_tui."""

from __future__ import annotations

from testagent.plan.device_manager import DeviceInfo
from testagent.plan.multi_device_tui import MultiDeviceTUI


class TestMultiDeviceTUI:
    def test_start_stop_does_not_crash(self) -> None:
        devices = [
            DeviceInfo(udid="emulator-5554", name="Pixel 6"),
        ]
        tui = MultiDeviceTUI(devices)
        tui.start()
        tui.update_log("emulator-5554", "Test message", "info")
        tui.update_summary("emulator-5554", "running")
        tui.stop()

    def test_multiple_devices(self) -> None:
        devices = [
            DeviceInfo(udid="emulator-5554", name="Pixel 6"),
            DeviceInfo(udid="192.168.1.100:5555", name="Xiaomi 13"),
            DeviceInfo(udid="R5CT11XXXX", name="Samsung S23"),
        ]
        tui = MultiDeviceTUI(devices)
        tui.start()
        for d in devices:
            tui.update_log(d.udid, "App launched", "success")
            tui.update_summary(d.udid, "running")
        tui.stop()
