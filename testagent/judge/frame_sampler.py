"""Frame sampler for CaseJudgeAgent.

Extracts key frames from screen recordings based on execution step timestamps.
Uses ffmpeg for frame extraction with fallback strategies.
"""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger
from testagent.plan.models import EvidenceItem, TestCase

_logger = get_logger(__name__)


@dataclass
class SampledFrame:
    """A single frame extracted from a recording."""
    timestamp_ms: int
    frame_path: str
    description: str  # e.g. "Step 3 (assert) - before"


async def sample_frames(
    tc: TestCase,
    output_dir: str,
    max_frames: int = 12,
) -> list[SampledFrame]:
    """Sample key frames from a test case's recording.

    Uses action-semantic-based sampling:
    - wait/sleep actions: frame before and after
    - assert actions: frame at assert time + 2-3s later
    - tap + wait combos: frame before tap and after wait
    - Error events: frame at error time

    Args:
        tc: The executed test case with evidence (recording path).
        output_dir: Directory to save sampled frames.
        max_frames: Maximum number of frames to extract.

    Returns:
        List of SampledFrame objects, sorted by timestamp.
    """
    # Find the recording file
    recording_path = _find_recording(tc)
    if not recording_path:
        _logger.warning("No recording found for %s, skipping frame sampling", tc.id)
        return []

    if not Path(recording_path).exists():
        _logger.warning("Recording file not found: %s", recording_path)
        return []

    # Check ffmpeg availability
    if not _ffmpeg_available():
        _logger.warning("ffmpeg not available, skipping frame sampling")
        return []

    # Calculate timestamps to sample
    timestamps = _calculate_timestamps(tc, max_frames)

    # Extract frames
    frames_dir = Path(output_dir) / "judge_frames" / tc.id
    frames_dir.mkdir(parents=True, exist_ok=True)

    sampled: list[SampledFrame] = []
    for i, (ts_ms, description) in enumerate(timestamps):
        frame_path = str(frames_dir / f"frame_{i:03d}.png")
        success = await _extract_frame(recording_path, ts_ms, frame_path)
        if success:
            sampled.append(SampledFrame(
                timestamp_ms=ts_ms,
                frame_path=frame_path,
                description=description,
            ))
        else:
            # Fallback: try nearby timestamps
            for offset in [500, -500, 1000, -1000]:
                fallback_path = str(frames_dir / f"frame_{i:03d}_fb.png")
                success = await _extract_frame(recording_path, ts_ms + offset, fallback_path)
                if success:
                    sampled.append(SampledFrame(
                        timestamp_ms=ts_ms + offset,
                        frame_path=fallback_path,
                        description=f"{description} (fallback +{offset}ms)",
                    ))
                    break

    return sampled


def _find_recording(tc: TestCase) -> str | None:
    """Find the recording file path from test case evidence."""
    for evidence in tc.execution.evidence:
        if evidence.type == "recording":
            return evidence.path
    return None


def _ffmpeg_available() -> bool:
    """Check if ffmpeg is available on the system."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _calculate_timestamps(tc: TestCase, max_frames: int) -> list[tuple[int, str]]:
    """Calculate timestamps to sample based on step execution semantics.

    Returns list of (timestamp_ms, description) tuples.
    """
    timestamps: list[tuple[int, str]] = []
    cumulative_ms = 0

    for step_exec in tc.execution.steps:
        step_start = cumulative_ms
        step_duration = step_exec.duration_ms or 3000  # default 3s if unknown
        step_end = step_start + step_duration

        action = step_exec.action or ""

        # Before each step (except the first launch)
        if step_exec.step > 1 and action != "launch":
            timestamps.append((step_start, f"Step {step_exec.step} ({action}) - before"))

        # After each step
        timestamps.append((step_end, f"Step {step_exec.step} ({action}) - after"))

        # For assert actions: also sample 2-3s later to check state persistence
        if action == "assert":
            timestamps.append((step_end + 2500, f"Step {step_exec.step} (assert) - persistence check"))

        # For wait actions: sample at the end (already covered by "after")
        # For error events: sample at the error point
        if not step_exec.success:
            timestamps.append((step_end, f"Step {step_exec.step} ({action}) - ERROR"))

        cumulative_ms = step_end

    # Always include a final frame
    timestamps.append((cumulative_ms, "Final state"))

    # Deduplicate and sort by timestamp
    seen = set()
    unique: list[tuple[int, str]] = []
    for ts, desc in timestamps:
        ts_key = ts // 1000  # deduplicate at second granularity
        if ts_key not in seen and ts >= 0:
            seen.add(ts_key)
            unique.append((ts, desc))

    # Sort by timestamp and limit
    unique.sort(key=lambda x: x[0])
    return unique[:max_frames]


async def _extract_frame(recording_path: str, timestamp_ms: int, output_path: str) -> bool:
    """Extract a single frame from a video file using ffmpeg.

    Args:
        recording_path: Path to the video file.
        timestamp_ms: Timestamp in milliseconds.
        output_path: Path to save the extracted frame.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    timestamp_s = max(0, timestamp_ms / 1000.0)

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-ss", str(timestamp_s),
            "-i", recording_path,
            "-frames:v", "1",
            "-y",  # overwrite
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=10
        )
        if process.returncode == 0 and Path(output_path).exists():
            return True
        else:
            _logger.debug(
                "ffmpeg frame extraction failed: %s",
                stderr.decode(errors="replace")[:200],
            )
            return False
    except asyncio.TimeoutError:
        _logger.debug("ffmpeg frame extraction timed out at %.1fs", timestamp_s)
        return False
    except Exception as e:
        _logger.debug("ffmpeg frame extraction error: %s", e)
        return False
