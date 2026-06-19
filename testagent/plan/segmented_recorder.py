"""Segmented screen recorder that handles the 180s adb screenrecord limit.

Splits recording into multiple segments when approaching the time limit,
then concatenates them into a single video using ffmpeg.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from testagent.common.logging import get_logger

_logger = get_logger(__name__)

# adb screenrecord has a 180s hard limit. Split at 170s to be safe.
_SEGMENT_LIMIT_S = 170
# Minimum segment duration to bother keeping (skip very short segments)
_MIN_SEGMENT_S = 2


@dataclass
class RecordingSegment:
    """A single recording segment."""
    index: int
    path: str
    duration_s: float = 0.0


class SegmentedRecorder:
    """Manages segmented screen recording with automatic splitting and concatenation.

    Usage:
        recorder = SegmentedRecorder(
            start_fn=app_start_recording,
            stop_fn=app_stop_recording,
            output_dir="/path/to/recordings",
            tc_id="TC-001",
        )
        await recorder.start()
        # ... execute test steps ...
        await recorder.stop()
        final_path = await recorder.concat()
    """

    def __init__(
        self,
        start_fn: Callable[[], Awaitable[dict[str, Any]]],
        stop_fn: Callable[[], Awaitable[dict[str, Any]]],
        output_dir: str,
        tc_id: str,
        segment_limit_s: int = _SEGMENT_LIMIT_S,
    ) -> None:
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._output_dir = Path(output_dir) / "recordings"
        self._tc_id = tc_id
        self._segment_limit_s = segment_limit_s

        self._segments: list[RecordingSegment] = []
        self._current_start_time: float = 0.0
        self._is_recording = False
        self._segment_counter = 0

    async def start(self) -> bool:
        """Start the first recording segment."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        return await self._start_new_segment()

    async def stop(self) -> None:
        """Stop the current recording segment."""
        if self._is_recording:
            await self._stop_current_segment()

    async def check_and_split(self) -> None:
        """Check if current segment is approaching the time limit and split if needed.

        Call this after each test step to ensure the recording doesn't hit the
        180s hard limit.
        """
        if not self._is_recording:
            return

        elapsed = time.time() - self._current_start_time
        if elapsed >= self._segment_limit_s:
            _logger.info(
                "SegmentedRecorder: segment %d reached %.0fs, splitting",
                self._segment_counter, elapsed,
            )
            # Stop current segment and start a new one
            await self._stop_current_segment()
            await self._start_new_segment()

    async def concat(self) -> str | None:
        """Concatenate all segments into a single video file.

        Returns the path to the final concatenated video, or None if no segments.
        """
        # Filter out very short segments
        valid = [s for s in self._segments if s.duration_s >= _MIN_SEGMENT_S]

        if not valid:
            _logger.warning("SegmentedRecorder: no valid segments to concatenate")
            return None

        if len(valid) == 1:
            # Only one segment, just rename it
            final_path = self._output_dir / f"{self._tc_id}.mp4"
            final_path.unlink(missing_ok=True)  # Delete existing file if present
            Path(valid[0].path).rename(final_path)
            _logger.info("SegmentedRecorder: single segment, saved to %s", final_path)
            return str(final_path)

        # Multiple segments: concatenate with ffmpeg
        final_path = self._output_dir / f"{self._tc_id}.mp4"
        concat_list = self._output_dir / f"{self._tc_id}_concat.txt"

        # Write ffmpeg concat list
        with open(concat_list, "w", encoding="utf-8") as f:
            for seg in valid:
                f.write(f"file '{seg.path}'\n")

        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                "-y",
                str(final_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )

            if process.returncode == 0 and final_path.exists():
                _logger.info(
                    "SegmentedRecorder: concatenated %d segments into %s",
                    len(valid), final_path,
                )
                # Clean up segment files and concat list
                self._cleanup_segments(valid, concat_list)
                return str(final_path)
            else:
                _logger.error(
                    "SegmentedRecorder: ffmpeg concat failed: %s",
                    stderr.decode(errors="replace")[:300],
                )
                # Fallback: return the first segment
                return valid[0].path

        except Exception as e:
            _logger.error("SegmentedRecorder: concat error: %s", e)
            return valid[0].path if valid else None

    async def _start_new_segment(self) -> bool:
        """Start a new recording segment."""
        self._segment_counter += 1
        segment_path = str(
            self._output_dir / f"{self._tc_id}_seg{self._segment_counter:03d}.mp4"
        )

        try:
            result = await asyncio.wait_for(self._start_fn(), timeout=15)
            if not result.get("error"):
                self._current_start_time = time.time()
                self._is_recording = True
                self._segments.append(RecordingSegment(
                    index=self._segment_counter,
                    path=segment_path,
                ))
                _logger.info(
                    "SegmentedRecorder: segment %d started", self._segment_counter,
                )
                return True
            else:
                _logger.warning(
                    "SegmentedRecorder: start failed: %s", result.get("error", "")[:80],
                )
                return False
        except Exception as e:
            _logger.warning("SegmentedRecorder: start error: %s", e)
            return False

    async def _stop_current_segment(self) -> None:
        """Stop the current recording segment and save it.

        Retries once after 5s if no video data is returned.
        If all retries fail, marks the segment as failed (no fallback screenshots).
        """
        if not self._is_recording:
            return

        for attempt in range(2):
            try:
                result = await asyncio.wait_for(self._stop_fn(), timeout=60)
                video_b64 = result.get("video_base64", "")

                if video_b64:
                    import base64
                    segment = self._segments[-1] if self._segments else None
                    if segment:
                        segment.duration_s = time.time() - self._current_start_time
                        segment_path = self._output_dir / f"{self._tc_id}_seg{segment.index:03d}.mp4"
                        segment_path.write_bytes(base64.b64decode(video_b64))
                        segment.path = str(segment_path)
                        _logger.info(
                            "SegmentedRecorder: segment %d saved (%.1fs)",
                            segment.index, segment.duration_s,
                        )
                        self._is_recording = False
                        return
                else:
                    if attempt == 0:
                        _logger.info("SegmentedRecorder: no video data, retrying in 5s...")
                        await asyncio.sleep(5)
                    else:
                        _logger.warning(
                            "SegmentedRecorder: stop returned no video data after retry: %s",
                            result.get("error", "unknown")[:80],
                        )

            except Exception as e:
                if attempt == 0:
                    _logger.info("SegmentedRecorder: stop error, retrying in 5s: %s", e)
                    await asyncio.sleep(5)
                else:
                    _logger.warning("SegmentedRecorder: stop error after retry: %s", e)

        self._is_recording = False

        self._is_recording = False

    def _cleanup_segments(
        self, segments: list[RecordingSegment], concat_list: Path
    ) -> None:
        """Clean up segment files after successful concatenation."""
        for seg in segments:
            try:
                Path(seg.path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            concat_list.unlink(missing_ok=True)
        except Exception:
            pass
