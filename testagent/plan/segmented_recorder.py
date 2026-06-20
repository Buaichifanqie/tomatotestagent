"""Segmented screen recorder — simple, reliable, no ffmpeg.

Each segment is a standalone valid MP4 recorded via ADB screenrecord.
No concatenation. Multiple segments are all passed to the Judge.

Key design:
- Use ``adb shell screenrecord --time-limit 180`` so the process exits
  naturally and writes a complete MP4 with a valid moov atom.
- When we need to stop early (TC finished before 180s), send SIGINT to
  the device-side screenrecord via ``adb shell pkill -2 screenrecord``,
  which makes it write the file header properly before exiting.
- Never kill the host ADB process — that truncates the MP4.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger

_logger = get_logger(__name__)

# screenrecord hard limit is 180s. Use it directly — let the process auto-exit.
_SEGMENT_LIMIT_S = 180
# Minimum file size to bother keeping (skip empty / 0-byte pulls)
_MIN_FILE_BYTES = 1024


class SegmentedRecorder:
    """Records screen in 180s segments. No concatenation — multiple MPs all passed to Judge.

    Usage:
        recorder = SegmentedRecorder(
            output_dir="/path/to/recordings",
            tc_id="TC-001",
        )
        await recorder.start()
        # ... execute test steps (check_and_split after each) ...
        await recorder.stop()
        all_paths = recorder.get_segments()
        for p in all_paths:
            tc.execution.evidence.append(EvidenceItem(type="recording", path=p))
    """

    def __init__(
        self,
        output_dir: str,
        tc_id: str,
    ) -> None:
        self._output_dir = Path(output_dir) / "recordings" / tc_id
        self._tc_id = tc_id

        self._segment_paths: list[str] = []   # local paths of saved segments
        self._device_path: str = ""            # current segment's device path
        self._local_path: str = ""             # current segment's target local path
        self._segment_counter = 0
        self._adb_process: asyncio.subprocess.Process | None = None
        self._is_recording = False

    # ── Public API ──────────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Start the first recording segment."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        return await self._start_segment()

    async def stop(self) -> None:
        """Stop the current recording segment cleanly (SIGINT → pull)."""
        if not self._is_recording:
            return
        await self._stop_current_segment()

    async def check_and_split(self) -> None:
        """If the 180s limit was reached (process auto-exited), start a new segment.

        Call this after each test step.
        """
        if not self._is_recording or self._adb_process is None:
            return

        # Check if the process has exited naturally (180s auto-timeout)
        if self._adb_process.returncode is not None:
            _logger.info(
                "SegmentedRecorder: segment %d auto-exited (180s limit)", self._segment_counter,
            )
            await self._pull_current_segment()
            await self._start_segment()

    def get_segments(self) -> list[str]:
        """Return list of all saved segment paths (valid MPs)."""
        return list(self._segment_paths)

    # ── Internal: segment lifecycle ─────────────────────────────────────────

    async def _start_segment(self) -> bool:
        """Start a new ADB screenrecord segment (auto-exits after 180s)."""
        self._segment_counter += 1
        self._device_path = f"/sdcard/{self._tc_id}_seg{self._segment_counter:03d}.mp4"
        self._local_path = str(
            self._output_dir / f"seg{self._segment_counter:03d}.mp4"
        )

        try:
            self._adb_process = await asyncio.create_subprocess_exec(
                "adb", "shell", "screenrecord",
                "--time-limit", str(_SEGMENT_LIMIT_S),
                "--bit-rate", "4000000",
                self._device_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._is_recording = True
            _logger.info(
                "SegmentedRecorder: segment %d started (limit=%ds)",
                self._segment_counter, _SEGMENT_LIMIT_S,
            )
            # Small wait for screenrecord to initialise
            await asyncio.sleep(1)
            return True
        except Exception as e:
            _logger.warning("SegmentedRecorder: start failed: %s", e)
            self._is_recording = False
            self._adb_process = None
            return False

    async def _stop_current_segment(self) -> None:
        """Stop the current ADB segment cleanly and pull the file.

        Sends SIGINT to the device-side screenrecord so it finalises the
        MP4 header, then pulls the file to the host.
        """
        if not self._is_recording or self._adb_process is None:
            return

        # ── Step 1: Send SIGINT to the remote screenrecord process ──
        # This makes screenrecord finalise the MP4 file and exit cleanly.
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "adb", "shell", "pkill", "-2", "screenrecord",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception as e:
            _logger.warning("SegmentedRecorder: pkill failed: %s", e)

        # ── Step 2: Wait for the adb shell screenrecord to exit ──
        try:
            await asyncio.wait_for(self._adb_process.wait(), timeout=15)
        except asyncio.TimeoutError:
            _logger.warning("SegmentedRecorder: screenrecord didn't exit after SIGINT, killing")
            self._adb_process.kill()
            await self._adb_process.wait()

        self._adb_process = None

        # ── Step 3: Pull the file from device ──
        await self._pull_current_segment()

    async def _pull_current_segment(self) -> None:
        """Pull the current segment's video file from the device.

        Safe to call after the process has exited (naturally at 180s or
        via SIGINT). The MP4 on the device is complete and valid.
        """
        if not self._device_path or not self._local_path:
            return

        try:
            pull = await asyncio.create_subprocess_exec(
                "adb", "pull", self._device_path, self._local_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await pull.wait()

            local = Path(self._local_path)
            if local.exists() and local.stat().st_size >= _MIN_FILE_BYTES:
                self._segment_paths.append(self._local_path)
                _logger.info(
                    "SegmentedRecorder: segment %d saved (%.1f MB)",
                    self._segment_counter,
                    local.stat().st_size / (1024 * 1024),
                )
            else:
                _logger.warning(
                    "SegmentedRecorder: segment %d pull failed (empty or too small)",
                    self._segment_counter,
                )
                local.unlink(missing_ok=True)

            # Clean up device file
            try:
                rm_proc = await asyncio.create_subprocess_exec(
                    "adb", "shell", "rm", "-f", self._device_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await rm_proc.wait()
            except Exception:
                pass

        except Exception as e:
            _logger.warning("SegmentedRecorder: pull error: %s", e)

        finally:
            self._is_recording = False
            self._adb_process = None
            self._device_path = ""
            self._local_path = ""
