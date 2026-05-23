from __future__ import annotations

from testagent.plan.models import PopupRule

DEFAULT_POPUP_RULES = [
    PopupRule(name="permission_dialog", target_text=["允许", "权限"], action="tap", button_text="允许"),
    PopupRule(name="update_dialog", target_text=["更新", "升级", "新版本"], action="tap", button_text="稍后"),
    PopupRule(name="privacy_agreement", target_text=["隐私", "协议", "同意"], action="tap", button_text="同意"),
    PopupRule(name="ad_dialog", target_text=["广告", "推广"], action="dismiss", button_text="关闭"),
    PopupRule(name="teen_mode", target_text=["青少年", "未成年"], action="tap", button_text="我知道了"),
    PopupRule(name="location_permission", target_text=["位置", "定位"], action="tap", button_text="允许"),
    PopupRule(name="notification_permission", target_text=["通知", "推送"], action="tap", button_text="允许"),
    PopupRule(name="network_error", target_text=["网络", "无网络", "连接失败"], action="dismiss", button_text="确定"),
    PopupRule(name="crash_recovery", target_text=["崩溃", "闪退", "异常退出"], action="dismiss", button_text="确定"),
]


class PopupHandler:
    """Detects and handles popup dialogs based on a set of rules."""

    def __init__(self, rules: list[PopupRule] | None = None) -> None:
        self._rules = rules if rules is not None else list(DEFAULT_POPUP_RULES)
        self._handled_count = 0

    def detect_popup(self, page_source: str) -> PopupRule | None:
        """Check page_source for known popup keywords (case-insensitive).

        Returns the first matching PopupRule, or None if no match found.
        """
        if not page_source:
            return None

        page_source_lower = page_source.lower()

        for rule in self._rules:
            for keyword in rule.target_text:
                if keyword.lower() in page_source_lower:
                    return rule

        return None

    def handle(self, page_source: str, driver=None) -> dict | None:
        """Detect popup and return handling info dict.

        The dict contains keys: rule_name, action, button_text.
        Returns None if no popup is detected.
        """
        rule = self.detect_popup(page_source)
        if rule is None:
            return None

        self._handled_count += 1
        return {
            "rule_name": rule.name,
            "action": rule.action,
            "button_text": rule.button_text,
        }

    @property
    def handled_count(self) -> int:
        """Return the number of popups handled."""
        return self._handled_count
