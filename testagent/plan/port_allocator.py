"""Port allocation for multi-device Appium instances.

Ensures every device gets a non-conflicting pair of ports:
- appium_port (default base 4723, step 2)
- system_port (default base 8200, step 2)

Example allocation for three devices:
  Device A → Appium: 4723, SystemPort: 8200
  Device B → Appium: 4725, SystemPort: 8202
  Device C → Appium: 4727, SystemPort: 8204
"""

from __future__ import annotations

import socket


class PortAllocator:
    """Allocate non-conflicting port pairs for Appium + UiAutomator2."""

    APPIUM_BASE = 4723
    SYSTEM_BASE = 8200
    PORT_STEP = 2

    def __init__(self) -> None:
        self._allocated: set[int] = set()

    def allocate(self) -> tuple[int, int]:
        """Return the next available (appium_port, system_port) pair."""
        appium_port = self._find_free(self.APPIUM_BASE)
        system_port = self._find_free(self.SYSTEM_BASE)
        self._allocated.add(appium_port)
        self._allocated.add(system_port)
        return appium_port, system_port

    def release(self, ports: tuple[int, int]) -> None:
        """Mark ports as free again."""
        self._allocated.discard(ports[0])
        self._allocated.discard(ports[1])

    def _find_free(self, start: int) -> int:
        port = start
        while port in self._allocated or not self._is_port_free(port):
            port += 1
        return port

    @staticmethod
    def _is_port_free(port: int) -> bool:
        """Return True if the port is not in use (quick TCP connect check)."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return False
        except (OSError, ConnectionRefusedError):
            return True
