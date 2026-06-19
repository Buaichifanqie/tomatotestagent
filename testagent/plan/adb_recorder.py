"""ADB screen recorder using exec-out screenrecord + ffmpeg.

Bypasses Appium's recording API (which uses adb screenrecord internally)
and instead streams the raw H264 output directly to ffmpeg on the host.
This is more reliable on emulators where the device-side encoder is unstable.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger

_logger = get_logger(__name__)


class ADBRecorder:
    """Records the device screen using `adb exec-out screenrecord` piped to ffmpeg.

    Unlike Appium's recording API (which records on-device then transfers),
    this streams raw H264 video directly to the host computer, bypassing
    the device's video encoder entirely. This is much more reliable on
    Android emulators.

    Usage:
        recorder = ADBRecorder(output_dir="/path/to/recordings", tc_id="TC-001")
        await recorder.start()
        # ... execute test steps ...
        await recorder.stop()
        # Recording saved to output_dir/recordings/TC-001.mp4
    """

    def __init__(
        self,
        output_dir: str,
        tc_id: str,
        adb_path: str = "adb",
    ) -> None:
        self._output_dir = Path(output_dir) / "recordings"
        self._tc_id = tc_id
        self._adb_path = adb_path
        self._process: asyncio.subprocess.Process | None = None
        self._output_path: Path | None = None
        self._is_recording = False

    async def start(self) -> bool:
        """Start recording by piping adb screenrecord to ffmpeg."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._output_path = self._output_dir / f"{self._tc_id}.mp4"

        try:
            # Use adb exec-out to stream raw H264 from the device,
            # pipe to ffmpeg to save as MP4 on the host.
            # No time limit - recording stops when we kill the process.
            cmd = (
                f"{self._adb_path} exec-out screenrecord "
                f"--output-format=h264 --size 540x960 - "
                f"| ffmpeg -i - -c copy -y \"{self._output_path}\""
            )
            self._process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._is_recording = True
            _logger.info("ADBRecorder: started recording to %s", self._output_path)
            # Give ffmpeg a moment to initialize
            await asyncio.sleep(1)
            return True
        except Exception as e:
            _logger.error("ADBRecorder: failed to start: %s", e)
            self._is_recording = False
            return False

    async def stop(self) -> str | None:
        """Stop recording and return the path to the saved video file.

        Returns:
            Path to the saved MP4 file, or None if recording failed.
        """
        if not self._is_recording or self._process is None:
            return None

        self._is_recording = False

        try:
            # Kill the adb process (sends SIGTERM to ffmpeg via pipe)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

            # Check if the file was created and has content
            if self._output_path and self._output_path.exists():
                size = self._output_path.stat().st_size
                if size > 0:
                    _logger.info(
                        "ADBRecorder: saved %s (%.1f MB)",
                        self._output_path,
                        size / (1024 * 1024),
                    )
                    return str(self._output_path)
                else:
                    _logger.warning("ADBRecorder: output file is empty")
                    self._output_path.unlink(missing_ok=True)
            else:
                _logger.warning("ADBRecorder: output file not created")

        except Exception as e:
            _logger.error("ADBRecorder: stop error: %s", e)

        return None

    @staticmethod
    def is_available(adb_path: str = "adb") -> bool:
        """Check if adb exec-out screenrecord is available."""
        try:
            result = subprocess.run(
                [adb_path, "exec-out", "screenrecord", "--help"],
                capture_output=True,
                timeout=5,
            )
            # screenrecord --help returns exit code 0 or 1
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
