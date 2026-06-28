"""CaseJudgeAgent — semantic-level test case judge.

Uses multimodal vision model with VIDEO input to evaluate test case
execution results. Uploads the recorded mp4 via Files API, then
sends the file_id to the Responses API for analysis.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testagent.common.logging import get_logger
from testagent.judge.log_digest import generate_log_digest, generate_steps_description
from testagent.plan.models import (
    ExecutionStatus,
    ExecutionVerdict,
    TestCase,
)

_logger = get_logger(__name__)

# Load the judge prompt template
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_JUDGE_PROMPT_TEMPLATE = (_PROMPTS_DIR / "judge_prompt.txt").read_text(encoding="utf-8")

# Confidence threshold below which the verdict is downgraded to NEED_REVIEW
CONFIDENCE_THRESHOLD = 0.7

_DEFAULT_API_URL = "https://ark.cn-beijing.volces.com/api/v3"
_DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"


@dataclass
class CaseJudgeResult:
    """Result from CaseJudgeAgent evaluation."""
    verdict: ExecutionVerdict
    confidence: float
    failure_category: str  # BUG / ENVIRONMENT / TEST_ISSUE / FLAKY / NONE
    failure_root_cause: str
    evidence: list[str] = field(default_factory=list)
    step_assessments: list[dict] = field(default_factory=list)
    reasoning: str = ""
    retry_recommended: bool = False


class CaseJudgeAgent:
    """Semantic-level test case judge using multimodal vision model with video input.

    Uploads the recorded mp4 via Files API, then sends the file_id to the
    Responses API for analysis. Uses separate config (judge_api_key/judge_model)
    from the execution engine's vision config.
    """

    def __init__(
        self,
        output_dir: str = "",
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        token_tracker: Any = None,
    ) -> None:
        self._output_dir = output_dir
        self._confidence_threshold = confidence_threshold
        self._token_tracker = token_tracker
        self._api_key: str = ""
        self._api_url: str = _DEFAULT_API_URL
        self._model: str = _DEFAULT_MODEL
        self._timeout: int = 120
        self._fps: float = 1.0
        self._config_loaded = False

    def _load_config(self) -> bool:
        """Load judge config from settings. Returns True if configured."""
        if self._config_loaded:
            return bool(self._api_key)
        self._config_loaded = True
        try:
            from testagent.config.settings import get_settings
            settings = get_settings()

            # Judge-specific config (independent of execution engine vision config)
            key = settings.judge_api_key.get_secret_value()
            if key:
                self._api_key = key
                self._api_url = settings.judge_api_url or settings.vision_api_url or _DEFAULT_API_URL
                self._model = settings.judge_model or settings.vision_model or _DEFAULT_MODEL
                self._timeout = settings.judge_timeout
                self._fps = settings.judge_fps
                return True

            # Fallback to execution engine vision config if judge config not set
            key = settings.vision_api_key.get_secret_value()
            if key:
                self._api_key = key
                self._api_url = settings.vision_api_url or _DEFAULT_API_URL
                self._model = settings.vision_model or _DEFAULT_MODEL
                self._timeout = settings.vision_timeout or 120
                self._fps = 1.0
                _logger.info("CaseJudgeAgent: using execution engine vision config as fallback")
                return True

            return False
        except Exception as e:
            _logger.warning("CaseJudgeAgent: failed to load config: %s", e)
            return False

    async def evaluate(
        self,
        tc: TestCase,
        level: str = "light",
    ) -> CaseJudgeResult:
        """Evaluate a test case by sending its recording video to the vision model.

        Args:
            tc: The executed test case with evidence (recording).
            level: "light" or "deep" (affects fps setting).

        Returns:
            CaseJudgeResult with verdict, category, and retry recommendation.
        """
        if not self._load_config():
            return CaseJudgeResult(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                failure_category="NONE",
                failure_root_cause="",
                reasoning="No vision API config available for CaseJudgeAgent",
            )

        # Find recording videos (supporting multiple 180s segments)
        recording_paths = self._find_recordings(tc)

        # Build prompt with segment info if multiple recordings
        frames_desc = self._build_frames_description(recording_paths)
        prompt = self._build_prompt(tc, frames_desc)

        raw = None
        if recording_paths:
            fps = self._fps if level == "light" else min(self._fps * 2, 3.0)
            raw = await self._call_vision_api_sdk(recording_paths, prompt, fps)
            # Skip httpx video fallback — it consistently fails with 400 errors
            # because base64 video payloads are too large for the Chat API.
            # Go straight to screenshot fallback instead.

        if not raw:
            # Both video paths failed → try screenshots as last resort
            if recording_paths:
                _logger.warning(
                    "CaseJudgeAgent: video upload failed for %s, trying screenshots",
                    tc.id,
                )
            else:
                _logger.warning("CaseJudgeAgent: no recording for %s, using screenshots", tc.id)
            screenshots = self._find_screenshots(tc)
            raw = await self._call_vision_api_screenshots(screenshots, prompt)

        if not raw:
            return CaseJudgeResult(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                failure_category="NONE",
                failure_root_cause="",
                reasoning="Vision API call failed (no recording or screenshots)",
            )

        # Parse response
        return self._parse_response(raw, tc)

    def _build_prompt(self, tc: TestCase, frames_description: str = "") -> str:
        """Build the judge prompt from test case data."""
        log_digest = generate_log_digest(tc)
        steps_desc = generate_steps_description(tc)

        if not frames_description:
            frames_description = "(已通过视频传入，请直接分析视频内容)"

        return _JUDGE_PROMPT_TEMPLATE.format(
            tc_id=tc.id,
            tc_goal=tc.expected_outcome or tc.title,
            tc_priority=tc.priority,
            tc_steps=steps_desc,
            log_digest=log_digest,
            frames_description=frames_description,
        )

    def _build_frames_description(self, recording_paths: list[str]) -> str:
        """Build description of video frames for the prompt.

        When multiple segments exist (180s split), tell the model the order
        and that they form a continuous recording.
        """
        n = len(recording_paths)
        if n == 0:
            return "(已通过截图传入，请分析截图内容)"
        elif n == 1:
            return "(已通过视频传入，请直接分析视频内容)"
        else:
            return (
                f"(已通过 {n} 段视频传入，按录制时间依次排列：第 1 段为测试开始部分，第 {n} 段为测试结束状态。"
                f"各段视频连续录制、前后衔接，请按顺序综合分析整个过程)"
            )

    def _resolve_path(self, path: str) -> Path:
        """Resolve an evidence path (relative or absolute) to an absolute Path.

        Evidence paths are stored relative to ``_output_dir`` for portability.
        If the path is already absolute, return it as-is.
        """
        p = Path(path)
        if p.is_absolute():
            return p
        return Path(self._output_dir) / p

    def _find_recordings(self, tc: TestCase) -> list[str]:
        """Find all recording file paths from test case evidence.

        Supports multiple 180s segments — all are returned and sent to the Judge.
        Paths are resolved from relative → absolute via ``_resolve_path``.
        """
        paths = []
        for evidence in tc.execution.evidence:
            if evidence.type == "recording":
                resolved = self._resolve_path(evidence.path)
                if resolved.exists():
                    paths.append(str(resolved))
        return paths

    def _find_screenshots(self, tc: TestCase) -> list[str]:
        """Find screenshot file paths from test case evidence (fallback when no recording)."""
        screenshots = []
        for evidence in tc.execution.evidence:
            if evidence.type == "screenshot":
                resolved = self._resolve_path(evidence.path)
                if resolved.exists():
                    screenshots.append(str(resolved))
        return screenshots

    async def _call_vision_api_screenshots(
        self, screenshot_paths: list[str], prompt: str
    ) -> str | None:
        """Send screenshots to vision API instead of video (fallback for emulator).

        Samples up to 8 screenshots evenly across the test flow so the model
        sees key moments from the entire execution, not just the end.
        """
        if not screenshot_paths:
            _logger.warning("CaseJudgeAgent: no screenshots available either")
            return None

        import base64
        try:
            # Sample screenshots evenly across the test (not just the last N)
            total = len(screenshot_paths)
            _logger.info("CaseJudgeAgent: %d screenshots available, sampling for judge", total)
            MAX_SCREENSHOTS = 8
            if total <= MAX_SCREENSHOTS:
                sampled = screenshot_paths
            else:
                # Take first, last, and evenly spaced in between
                step = (total - 1) / (MAX_SCREENSHOTS - 1)
                indices = [int(round(i * step)) for i in range(MAX_SCREENSHOTS)]
                sampled = [screenshot_paths[i] for i in indices]
                _logger.info(
                    "CaseJudgeAgent: sampling %d/%d screenshots (indices: %s)",
                    MAX_SCREENSHOTS, total, indices,
                )

            images = []
            for path in sampled:
                img_size = Path(path).stat().st_size
                # Skip screenshots larger than 5MB to avoid API errors
                if img_size > 5 * 1024 * 1024:
                    _logger.warning("CaseJudgeAgent: skipping large screenshot %s (%.1f MB)", path, img_size / (1024*1024))
                    continue
                b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                images.append(b64)

            content_parts = []
            for b64 in images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
            content_parts.append({"type": "text", "text": prompt})

            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": content_parts}],
                "temperature": 0.1,
                "max_tokens": 2048,
            }
            endpoint = self._api_url.rstrip("/") + "/chat/completions"

            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
                if response.status_code != 200:
                    _logger.error(
                        "CaseJudgeAgent: screenshot fallback HTTP %d: %s",
                        response.status_code, response.text[:200],
                    )
                    return None
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            _logger.error("CaseJudgeAgent: screenshot fallback failed: %s: %s", type(e).__name__, e)
            return None

    async def _call_vision_api_sdk(
        self, video_paths: list[str], prompt: str, fps: float
    ) -> str | None:
        """Upload videos via Files API and analyze via Responses API.

        Supports multiple video files (for 180s segments) — uploads all of
        them and sends all file_ids in one API call so the model sees the
        full test flow.
        """
        try:
            from volcenginesdkarkruntime import Ark

            client = Ark(
                base_url=self._api_url,
                api_key=self._api_key,
            )

            # Step 1: Upload ALL video segments via Files API
            file_ids: list[str] = []
            for vp in video_paths:
                # Check file size — skip obviously corrupted/tiny files
                import os
                file_size = os.path.getsize(vp)
                if file_size < 1024:
                    _logger.warning("CaseJudgeAgent: skipping tiny video %s (%d bytes)", vp, file_size)
                    continue

                upload_path = vp

                _logger.info("CaseJudgeAgent: uploading video %s (%.1f MB, fps=%.1f)",
                             upload_path, os.path.getsize(upload_path) / (1024 * 1024), fps)
                with open(upload_path, "rb") as f:
                    file_obj = client.files.create(
                        file=f,
                        purpose="user_data",
                        preprocess_configs={
                            "video": {
                                "fps": fps,
                            }
                        },
                    )
                file_id = file_obj.id
                _logger.info("CaseJudgeAgent: uploaded, file_id=%s", file_id)

                # Wait for processing with explicit polling
                import time as _time
                for _poll in range(30):  # max 60 seconds
                    _time.sleep(2)
                    file_info = client.files.retrieve(file_id)
                    file_status = getattr(file_info, "status", "unknown")
                    if file_status != "processing":
                        break
                else:
                    file_status = "timeout"

                if file_status == "failed":
                    _logger.warning("CaseJudgeAgent: file %s processing failed, skipping", file_id)
                    continue
                if file_status == "timeout":
                    _logger.warning("CaseJudgeAgent: file %s processing timed out, skipping", file_id)
                    continue
                _logger.info("CaseJudgeAgent: file %s processed (status=%s)", file_id, file_status)
                file_ids.append(file_id)

            if not file_ids:
                _logger.warning("CaseJudgeAgent: no videos successfully processed")
                return None

            # Step 2: Build content array with all videos + prompt
            content: list[dict] = []
            for fid in file_ids:
                content.append({"type": "input_video", "file_id": fid})
            content.append({"type": "input_text", "text": prompt})

            # Step 3: Call Responses API with all videos
            response = client.responses.create(
                model=self._model,
                input=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
            )

            # Record token usage and print immediately
            if self._token_tracker and hasattr(response, "usage") and response.usage:
                usage = response.usage
                pt = getattr(usage, "input_tokens", 0) or 0
                ct = getattr(usage, "output_tokens", 0) or 0
                tt = getattr(usage, "total_tokens", 0) or 0
                self._token_tracker.record(
                    category="judge",
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    total_tokens=tt,
                )
                if tt > 0:
                    _detail = f"{pt}↑ {ct}↓ = {tt}" if pt and ct else str(tt)
                    print(f"      \033[33m[Judge tokens] {_detail}\033[0m")

            # Extract text from response
            if hasattr(response, "output") and response.output:
                for item in response.output:
                    if hasattr(item, "content") and item.content:
                        for block in item.content:
                            if hasattr(block, "text"):
                                return block.text
            # Fallback: try to get content directly
            if hasattr(response, "content"):
                return str(response.content)

            _logger.warning("CaseJudgeAgent: unexpected response format")
            return None

        except Exception as e:
            _logger.error(
                "CaseJudgeAgent: SDK call failed: %s: %s",
                type(e).__name__, e,
                extra={"extra_data": {
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "api_url": self._api_url,
                    "model": self._model,
                    "api_key_present": bool(self._api_key),
                }},
            )
            return None

    async def _call_vision_api_httpx(
        self, video_paths: list[str], prompt: str, fps: float
    ) -> str | None:
        """Fallback: call vision API via httpx + base64 when SDK is unavailable.

        Only sends the LAST video segment (most recent, shows final test state)
        since httpx inline base64 is limited in payload size.
        Skips videos larger than 10MB to avoid API 400 errors.
        """
        import base64
        import httpx
        import os

        # Use the last (most recent) segment — it shows the most relevant state
        vp = video_paths[-1]

        # Skip large videos — base64 inline has payload limits
        file_size = os.path.getsize(vp)
        MAX_HTTPX_VIDEO_BYTES = 10 * 1024 * 1024  # 10MB
        if file_size > MAX_HTTPX_VIDEO_BYTES:
            _logger.warning(
                "CaseJudgeAgent: httpx fallback skipped — video too large (%.1f MB > 10 MB limit)",
                file_size / (1024 * 1024),
            )
            return None

        try:
            video_b64 = base64.b64encode(Path(vp).read_bytes()).decode("ascii")
        except Exception as e:
            _logger.error("CaseJudgeAgent: failed to read video for httpx fallback: %s", e)
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": f"data:video/mp4;base64,{video_b64}",
                                "fps": fps,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }
        endpoint = self._api_url.rstrip("/") + "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            _logger.error("CaseJudgeAgent: httpx fallback failed: %s", e)
            return None

    def _parse_response(self, raw: str, tc: TestCase) -> CaseJudgeResult:
        """Parse the LLM's JSON response into a CaseJudgeResult."""
        parsed = self._extract_json(raw)

        if not parsed:
            _logger.warning("CaseJudgeAgent: Failed to parse response, returning INCONCLUSIVE")
            return CaseJudgeResult(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.3,
                failure_category="NONE",
                failure_root_cause="",
                reasoning=f"Failed to parse response: {raw[:200]}",
            )

        verdict_str = parsed.get("verdict", "PASS").upper()
        confidence = float(parsed.get("confidence", 0.5))
        reasoning = parsed.get("reasoning", "")
        failure_category = parsed.get("failure_category", "NONE")
        failure_root_cause = parsed.get("failure_root_cause", "")
        evidence = parsed.get("evidence", [])
        step_assessments = parsed.get("step_assessments", [])

        verdict_map = {
            "PASS": ExecutionVerdict.PASS,
            "FAIL": ExecutionVerdict.FAIL,
            "NEED_REVIEW": ExecutionVerdict.NEED_REVIEW,
            "BLOCKED": ExecutionVerdict.BLOCKED,
            "INCONCLUSIVE": ExecutionVerdict.INCONCLUSIVE,
        }
        verdict = verdict_map.get(verdict_str, ExecutionVerdict.NEED_REVIEW)

        if confidence < self._confidence_threshold:
            _logger.info(
                "CaseJudgeAgent: confidence %.2f < threshold %.2f, downgrading to NEED_REVIEW",
                confidence, self._confidence_threshold,
            )
            verdict = ExecutionVerdict.NEED_REVIEW

        return CaseJudgeResult(
            verdict=verdict,
            confidence=confidence,
            failure_category=failure_category,
            failure_root_cause=failure_root_cause,
            evidence=evidence if isinstance(evidence, list) else [str(evidence)],
            step_assessments=step_assessments if isinstance(step_assessments, list) else [],
            reasoning=reasoning,
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract JSON object from LLM response text."""
        text = text.strip()
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        fence_match = re.search(r"```(?:json)?\s*\n?(.+?)\n?```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break

        return None


def should_invoke_judge(tc: TestCase) -> tuple[bool, str]:
    """Determine if a test case needs CaseJudgeAgent evaluation.

    All executed TCs are judged (no exceptions). Only ABORTED TCs are skipped.

    Returns (should_invoke, level) where level is "light" or "deep".
    """
    # Only skip ABORTED TCs (they never ran, nothing to judge)
    if tc.execution.status == ExecutionStatus.ABORTED:
        return False, ""

    # Had retries → deep judgment (more complex analysis needed)
    if tc.execution.retries > 0:
        return True, "deep"

    # Everything else → light judgment
    return True, "light"
