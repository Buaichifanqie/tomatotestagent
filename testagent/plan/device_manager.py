"""Device discovery and lifecycle management for multi-device testing.

``DeviceManager`` is the central coordinator:
1. Discover connected devices via ``adb devices``.
2. Assign each device a unique port pair via ``PortAllocator``.
3. Start per-device Appium servers via ``AppiumManager``.
4. Provide methods to stop all devices cleanly.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

from testagent.common.appium_manager import AppiumManager
from testagent.common.adb_utils import adb_command
from testagent.common.logging import get_logger
from testagent.plan.port_allocator import PortAllocator

logger = get_logger(__name__)


@dataclass
class DeviceInfo:
    udid: str
    name: str = ""
    status: str = "device"
    appium_port: int = 4723
    system_port: int = 8200

    @property
    def appium_url(self) -> str:
        return f"http://localhost:{self.appium_port}"


@dataclass
class DevicePlanAssignment:
    device: DeviceInfo
    plan_path: str


class DeviceManager:
    """Discover, allocate, and manage devices for parallel testing."""

    def __init__(
        self,
        port_allocator: Optional[PortAllocator] = None,
        appium_manager: Optional[AppiumManager] = None,
    ) -> None:
        self.port_allocator = port_allocator or PortAllocator()
        self.appium_manager = appium_manager or AppiumManager()
        self.devices: list[DeviceInfo] = []

    def discover_devices(self) -> list[DeviceInfo]:
        """List connected devices via ``adb devices -l``.

        Returns only devices whose status is ``device`` (online).
        """
        import subprocess
        try:
            result = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception as exc:
            logger.error("Failed to run adb devices", extra={"extra_data": {"error": str(exc)}})
            return []

        devices: list[DeviceInfo] = []
        for line in result.stdout.splitlines():
            if "device" not in line or "devices" in line:
                continue
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            udid = parts[0]
            status = parts[1]
            if status != "device":
                continue

            name = udid
            for token in parts[2:]:
                if token.startswith("model:"):
                    name = token.split(":", 1)[1].replace("_", " ")
                    break

            devices.append(DeviceInfo(udid=udid, name=name, status=status))

        self.devices = devices
        return devices

    async def prepare_device(self, device: DeviceInfo) -> DeviceInfo:
        """Allocate ports and start Appium for a device. Returns updated DeviceInfo."""
        appium_port, system_port = self.port_allocator.allocate()
        device.appium_port = appium_port
        device.system_port = system_port

        log_path = f"appium_{device.udid.replace(':', '_')}.log"
        await self.appium_manager.ensure_appium_running(
            udid=device.udid,
            port=appium_port,
            log_path=log_path,
        )
        logger.info("Device ready", extra={"extra_data": {
            "udid": device.udid, "appium_port": appium_port, "system_port": system_port,
        }})
        return device

    async def prepare_all(self, devices: list[DeviceInfo]) -> list[DeviceInfo]:
        """Prepare all devices in parallel."""
        tasks = [self.prepare_device(d) for d in devices]
        return await asyncio.gather(*tasks)

    async def teardown_device(self, udid: str) -> None:
        """Stop Appium for a single device and release ports."""
        await self.appium_manager.stop(udid)

    async def teardown_all(self) -> None:
        """Stop all Appium servers and release all ports."""
        await self.appium_manager.stop_all()
