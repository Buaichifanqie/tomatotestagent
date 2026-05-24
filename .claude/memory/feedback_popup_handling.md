---
name: popup-handling-strategy
description: Lessons learned about Android popup handling in automated testing with text-based and vision-based approaches
metadata:
  type: feedback
---

When handling Android popups during automated test execution, use the following approach in order:
1. **Text-based dismissal** with PopupHandler and UiSelector exact match first, then textContains fallback
2. **Vision fallback** when text tap fails — take screenshot, ask vision model to find dismiss button coordinates, tap at pixel coords
3. **False positive suppression**: track `_suppressed_rules` per step so if vision confirms no popup, skip further detection of that rule

**Why:** Text-based keywords like "更新" easily match normal app UI text (e.g., pull-to-refresh), causing false positives that waste 20-30s on vision API calls. Making keywords very specific (e.g., "发现新版本" instead of "更新") and adding suppression prevents this.

**How to apply:** When adding new popup rules, use the most specific text possible — prefer multi-character phrases unique to actual dialog buttons. Avoid single common words.

Tap retry: When a tap fails with "no such element", retry once after 2s wait. This handles the case where the UI is still settling after popup dismissal or first-launch flow (e.g., after `pm clear` + privacy agreement dismissal).
