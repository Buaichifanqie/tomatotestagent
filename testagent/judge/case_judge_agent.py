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

        # Find the recording video
        recording_path = self._find_recording(tc)
        if not recording_path or not Path(recording_path).exists():
            _logger.warning("CaseJudgeAgent: no recording found for %s", tc.id)
            return CaseJudgeResult(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                failure_category="NONE",
                failure_root_cause="",
                reasoning="No recording file found",
            )

        # Build prompt
        prompt = self._build_prompt(tc)

        # Set fps based on level
        fps = self._fps if level == "light" else min(self._fps * 2, 3.0)

        # Call vision API with video (SDK preferred, httpx fallback)
        raw = await self._call_vision_api_sdk(recording_path, prompt, fps)
        if not raw:
            _logger.info("CaseJudgeAgent: SDK failed, falling back to httpx + base64")
            raw = await self._call_vision_api_httpx(recording_path, prompt, fps)
        if not raw:
            return CaseJudgeResult(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                failure_category="NONE",
                failure_root_cause="",
                reasoning="Vision API call failed",
            )

        # Parse response
        return self._parse_response(raw, tc)

    def _build_prompt(self, tc: TestCase) -> str:
        """Build the judge prompt from test case data."""
        log_digest = generate_log_digest(tc)
        steps_desc = generate_steps_description(tc)

        return _JUDGE_PROMPT_TEMPLATE.format(
            tc_id=tc.id,
            tc_goal=tc.expected_outcome or tc.title,
            tc_priority=tc.priority,
            tc_steps=steps_desc,
            log_digest=log_digest,
            frames_description="(已通过视频传入，请直接分析视频内容)",
        )

    def _find_recording(self, tc: TestCase) -> str | None:
        """Find the recording file path from test case evidence."""
        for evidence in tc.execution.evidence:
            if evidence.type == "recording":
                return evidence.path
        return None

    async def _call_vision_api_sdk(
        self, video_path: str, prompt: str, fps: float
    ) -> str | None:
        """Upload video via Files API and analyze via Responses API."""
        try:
            from volcenginesdkarkruntime import Ark

            client = Ark(
                base_url=self._api_url,
                api_key=self._api_key,
            )

            # Step 1: Upload video via Files API
            _logger.info("CaseJudgeAgent: uploading video %s (fps=%.1f)", video_path, fps)
            with open(video_path, "rb") as f:
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

            # Step 2: Wait for processing
            client.files.wait_for_processing(file_id)
            _logger.info("CaseJudgeAgent: file processed")

            # Step 3: Call Responses API with video
            response = client.responses.create(
                model=self._model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_video",
                                "file_id": file_id,
                            },
                            {
                                "type": "input_text",
                                "text": prompt,
                            },
                        ],
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
            _logger.error("CaseJudgeAgent: SDK call failed: %s", e)
            return None

    async def _call_vision_api_httpx(
        self, video_path: str, prompt: str, fps: float
    ) -> str | None:
        """Fallback: call vision API via httpx + base64 when SDK is unavailable."""
        import base64

        try:
            video_b64 = base64.b64encode(Path(video_path).read_bytes()).decode("ascii")
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
