"""Test case execution order optimizer.

Groups test cases by required_state to minimize state transitions,
then sorts within each group by priority.
"""
from __future__ import annotations

import re

from testagent.plan.models import TestCase

# ── Dimension mapping ────────────────────────────────────────────
# Each state belongs to one dimension.  Negation in a dimension
# suppresses positive matches in the SAME dimension only.
_DIMENSIONS: dict[str, str] = {
    "logged_in": "auth",
    "logged_out": "auth",
    "network_on": "network",
    "network_off": "network",
}

# Negation patterns must be checked BEFORE positive patterns.
# "未登录页面展示" should NOT match logged_in despite containing "登录".
_NEGATION_PATTERNS = [
    (r"未登录|游客|未登入|guest|not.?logged", "logged_out"),
    (r"断网|无网络|离线|offline|no.?network|network.?off", "network_off"),
]

_POSITIVE_PATTERNS = [
    (r"登录|登入|logged.?in", "logged_in"),
    (r"登出|退出登录|logout|sign.?out", "logged_out"),
    (r"联网|有网|恢复网络|network.?on", "network_on"),
]

# States that steps can trigger
_STEP_PATTERNS = [
    (r"登出|退出登录|logout|sign.?out", "logged_out"),
    (r"登录|登入|login|sign.?in", "logged_in"),
]

# Group ordering: logged_in first, logged_out last, others in middle
_GROUP_ORDER = {
    "logged_in": 0,
    "network_off": 10,
    "network_on": 11,
    "logged_out": 99,
}

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _infer_state(tc: TestCase) -> set[str]:
    """Infer required_state from title and steps.

    Returns a set of state strings (e.g. {"logged_in"}, {"network_off"}, or set()).
    Does NOT mutate the original TestCase.
    """
    # If LLM already provided states, use them
    if tc.required_state:
        return set(tc.required_state)

    text = tc.title.lower()

    # 1. Collect negation matches, track which dimensions are negated
    states: set[str] = set()
    negated_dims: set[str] = set()
    for pattern, state in _NEGATION_PATTERNS:
        if re.search(pattern, text):
            states.add(state)
            negated_dims.add(_DIMENSIONS[state])

    # 2. Positive patterns — skip if same dimension was already negated
    #    e.g. "断网且已登录" → negation matches network_off (network dim),
    #    but auth dim is still open → logged_in can match.
    for pattern, state in _POSITIVE_PATTERNS:
        if _DIMENSIONS[state] in negated_dims:
            continue
        if re.search(pattern, text):
            states.add(state)

    if states:
        return states

    # Fallback: check steps
    for step in tc.steps:
        target = (step.target or "").lower()
        value = (step.value or "").lower()
        combined = target + value
        for pattern, state in _STEP_PATTERNS:
            if re.search(pattern, combined):
                return {state}

    return set()


def _state_distance(current: set[str], target: set[str]) -> int:
    """Dimension-aware state distance.

    Counts transitions per dimension:
    - same value → 0
    - different value in same dimension → 1 (switch)
    - present only in current → 1 (need to undo)
    - present only in target → 1 (need to set up)
    """
    current_by_dim = {_DIMENSIONS.get(s, s): s for s in current}
    target_by_dim = {_DIMENSIONS.get(s, s): s for s in target}

    cost = 0
    for dim in set(current_by_dim) | set(target_by_dim):
        in_cur = dim in current_by_dim
        in_tgt = dim in target_by_dim
        if in_cur and in_tgt:
            if current_by_dim[dim] != target_by_dim[dim]:
                cost += 1  # switch within dimension
        elif in_cur or in_tgt:
            cost += 1  # add or remove
    return cost


def _has_state_conflict(current: set[str], needed: set[str]) -> bool:
    """Check if any dimension has a conflicting value between current and needed.

    Conflicts:
    1. Same dimension, different value (e.g. current=logged_in, needed=logged_out)
    2. Extra dimension in current that needed doesn't specify
       (e.g. current={logged_in, network_off}, needed={logged_in} — network dim is extra)

    Default cases (needed is empty) never conflict — they don't care about state.
    """
    if not needed:
        return False

    current_by_dim = {_DIMENSIONS[s]: s for s in current if s in _DIMENSIONS}
    needed_by_dim = {_DIMENSIONS[s]: s for s in needed if s in _DIMENSIONS}

    # Same dimension, different value
    for dim, needed_state in needed_by_dim.items():
        if dim in current_by_dim and current_by_dim[dim] != needed_state:
            return True

    # Extra dimension in current that needed doesn't specify
    for dim in current_by_dim:
        if dim not in needed_by_dim:
            return True

    return False


def _get_primary_group(states: set[str]) -> str:
    """Get the primary group name for sorting. Returns the state with lowest group order."""
    if not states:
        return "default"
    return min(states, key=lambda s: _GROUP_ORDER.get(s, 50))


def reorder_for_execution(test_cases: list[TestCase]) -> list[TestCase]:
    """Infer required_state for each TC but preserve the original execution order.

    States are inferred so the execution engine can manage device state
    transitions, but the order is kept exactly as generated/presented to
    the user.
    """
    for tc in test_cases:
        if not tc.required_state:
            tc.required_state = sorted(_infer_state(tc))
    return list(test_cases)
