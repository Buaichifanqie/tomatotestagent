"""Unified ADB command execution with device targeting.

All multi-device ADB operations go through ``adb_command()`` to ensure the
``-s <udid>`` flag is always present.
"""

from __future__ import annotations

import subprocess
from typing import Any

from testagent.common.logging import get_logger

logger = get_logger(__name__)


def adb_command(
    udid: str,
    *args: str,
    capture_output: bool = False,
    text: bool = False,
    timeout: int = 10,
    encoding: str = "utf-8",
    errors: str = "replace",
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run an ADB command targeting a specific device.

    Prepends ``adb -s <udid>`` to *args so every call targets the correct
    device.  If ``udid`` is empty (single-device mode), runs plain ``adb``
    without ``-s`` for backward compatibility.

    Accepts the same keyword arguments as ``subprocess.run``.

    Args:
        udid: Device serial (e.g. ``emulator-5554``). Empty string = no device flag.
        *args: ADB subcommand and its arguments.
        **kwargs: Forwarded to ``subprocess.run``.

    Returns:
        ``subprocess.CompletedProcess`` from the underlying call.
    """
    if udid:
        cmd = ["adb", "-s", udid, *args]
    else:
        cmd = ["adb", *args]
    logger.debug("adb_command: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=capture_output, text=text,
                          timeout=timeout, encoding=encoding, errors=errors,
                          **kwargs)
