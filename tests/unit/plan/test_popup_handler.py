from __future__ import annotations

import pytest

from testagent.plan.models import PopupRule
from testagent.plan.popup_handler import DEFAULT_POPUP_RULES, PopupHandler


class TestDefaultRules:
    """Default popup rules are loaded correctly."""

    def test_default_rules_count(self):
        handler = PopupHandler()
        assert len(handler._rules) == 9

    def test_default_rules_content(self):
        handler = PopupHandler()
        rule_names = {r.name for r in handler._rules}
        expected = {
            "permission_dialog",
            "update_dialog",
            "privacy_agreement",
            "ad_dialog",
            "teen_mode",
            "location_permission",
            "notification_permission",
            "network_error",
            "crash_recovery",
        }
        assert rule_names == expected

    def test_default_rules_constant_matches_defaults(self):
        assert len(DEFAULT_POPUP_RULES) == 9


class TestCustomRules:
    """Custom rules override defaults."""

    def test_custom_rules_used(self):
        custom = [
            PopupRule(name="custom1", target_text=["foo"], action="tap", button_text="foo"),
        ]
        handler = PopupHandler(rules=custom)
        assert len(handler._rules) == 1
        assert handler._rules[0].name == "custom1"

    def test_empty_custom_rules(self):
        handler = PopupHandler(rules=[])
        assert handler._rules == []


class TestDetectPopup:
    """detect_popup method behavior."""

    def test_no_match_returns_none(self):
        handler = PopupHandler()
        page_source = "<xml>nothing relevant here</xml>"
        assert handler.detect_popup(page_source) is None

    def test_match_returns_rule(self):
        handler = PopupHandler()
        page_source = "请允许应用获取权限"
        rule = handler.detect_popup(page_source)
        assert rule is not None
        assert rule.name == "permission_dialog"

    def test_match_precedence(self):
        handler = PopupHandler()
        page_source = "广告推广"
        rule = handler.detect_popup(page_source)
        assert rule is not None
        assert rule.name == "ad_dialog"

    def test_empty_string_returns_none(self):
        handler = PopupHandler()
        assert handler.detect_popup("") is None

    def test_case_insensitive_matching(self):
        handler = PopupHandler()
        page_source = "ALLOW 权限 PERMISSION"
        rule = handler.detect_popup(page_source)
        assert rule is not None
        assert rule.name == "permission_dialog"

    def test_partial_word_match(self):
        handler = PopupHandler()
        page_source = "需要处理网络连接问题"
        rule = handler.detect_popup(page_source)
        assert rule is not None
        assert rule.name == "network_error"


class TestHandle:
    """handle method behavior."""

    def test_handle_returns_info_dict_on_match(self):
        handler = PopupHandler()
        page_source = "请允许应用获取权限"
        result = handler.handle(page_source)
        assert result is not None
        assert result["rule_name"] == "permission_dialog"
        assert result["action"] == "tap"
        assert result["button_text"] == "允许"

    def test_handle_returns_none_on_no_match(self):
        handler = PopupHandler()
        page_source = "nothing relevant"
        assert handler.handle(page_source) is None

    def test_handle_increments_count(self):
        handler = PopupHandler()
        page_source = "请允许应用获取权限"
        assert handler.handled_count == 0
        handler.handle(page_source)
        assert handler.handled_count == 1

    def test_handle_no_match_does_not_increment(self):
        handler = PopupHandler()
        page_source = "nothing relevant"
        handler.handle(page_source)
        assert handler.handled_count == 0

    def test_handle_multiple_times(self):
        handler = PopupHandler()
        handler.handle("请允许应用获取权限")
        handler.handle("有新版本更新")
        handler.handle("nothing")
        assert handler.handled_count == 2

    def test_handle_button_text_empty_for_dismiss(self):
        """ad_dialog has action=dismiss and button_text=关闭."""
        handler = PopupHandler()
        page_source = "广告推广内容"
        result = handler.handle(page_source)
        assert result is not None
        assert result["action"] == "dismiss"
        assert result["button_text"] == "关闭"
