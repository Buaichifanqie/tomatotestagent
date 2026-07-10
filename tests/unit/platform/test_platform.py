# tests/unit/platform/test_platform.py
from __future__ import annotations

import pytest
from testagent.platform.factory import PlatformFactory
from testagent.platform.android_platform import AndroidPlatform
from testagent.platform.ios_platform import iOSPlatform
from testagent.platform.interface import AbstractPlatform, BaseRecorder


class TestPlatformFactory:
    def test_create_android(self) -> None:
        platform = PlatformFactory.create("android")
        assert isinstance(platform, AndroidPlatform)

    def test_create_ios(self) -> None:
        platform = PlatformFactory.create("ios")
        assert isinstance(platform, iOSPlatform)

    def test_create_case_insensitive(self) -> None:
        for name in ("Android", "ANDROID", "iOS", "Ios"):
            platform = PlatformFactory.create(name)
            assert isinstance(platform, AbstractPlatform)

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown platform"):
            PlatformFactory.create("windows")

    def test_list_platforms(self) -> None:
        platforms = PlatformFactory.list_platforms()
        assert "android" in platforms
        assert "ios" in platforms


class TestAndroidPlatform:
    def setup_method(self) -> None:
        self.platform = PlatformFactory.create("android")

    def test_platform_properties(self) -> None:
        assert self.platform.platform_name == "Android"
        assert self.platform.automation_name == "UiAutomator2"

    def test_build_capabilities(self) -> None:
        caps = self.platform.build_capabilities(udid="emulator-5554")
        assert caps["platformName"] == "Android"
        assert caps["appium:automationName"] == "UiAutomator2"
        assert caps["appium:udid"] == "emulator-5554"
        assert caps["appium:allowInsecure"] == "*:adb_shell"
        assert "appium:systemPort" in caps

    def test_build_capabilities_custom_port(self) -> None:
        caps = self.platform.build_capabilities(udid="emulator-5556", system_port=8300)
        assert caps["appium:systemPort"] == 8300

    def test_find_element_strategies(self) -> None:
        strategies = self.platform.get_find_element_strategies()
        assert "uiautomator" in strategies
        assert "ios_predicate" not in strategies

    def test_default_selector_strategy(self) -> None:
        assert self.platform.get_default_selector_strategy() == "uiautomator"

    def test_appium_args(self) -> None:
        args = self.platform.get_appium_args()
        assert "--allow-insecure" in args
        assert "*:adb_shell" in args


class TestiOSPlatform:
    def setup_method(self) -> None:
        self.platform = PlatformFactory.create("ios")

    def test_platform_properties(self) -> None:
        assert self.platform.platform_name == "iOS"
        assert self.platform.automation_name == "XCUITest"

    def test_build_capabilities(self) -> None:
        caps = self.platform.build_capabilities(udid="00008110-xxxxxxxx")
        assert caps["platformName"] == "iOS"
        assert caps["appium:automationName"] == "XCUITest"
        assert caps["appium:udid"] == "00008110-xxxxxxxx"
        assert caps["appium:autoAcceptAlerts"] is True
        assert "appium:allowInsecure" not in caps
        assert "appium:wdaLocalPort" in caps

    def test_build_capabilities_custom_wda(self) -> None:
        caps = self.platform.build_capabilities(udid="test-udid", wda_local_port=8101)
        assert caps["appium:wdaLocalPort"] == 8101

    def test_find_element_strategies(self) -> None:
        strategies = self.platform.get_find_element_strategies()
        assert "ios_predicate" in strategies
        assert "ios_class_chain" in strategies
        assert "uiautomator" not in strategies

    def test_default_selector_strategy(self) -> None:
        assert self.platform.get_default_selector_strategy() == "ios_predicate"

    def test_appium_args(self) -> None:
        args = self.platform.get_appium_args()
        assert args == []


class TestRecorder:
    def test_android_recorder_type(self) -> None:
        platform = PlatformFactory.create("android")
        rec = platform.create_recorder("/tmp", "TC-001")
        assert isinstance(rec, BaseRecorder)

    def test_ios_recorder_type(self) -> None:
        platform = PlatformFactory.create("ios")
        rec = platform.create_recorder("/tmp", "TC-001")
        assert isinstance(rec, BaseRecorder)

    def test_recorder_initial_state(self) -> None:
        from testagent.platform.android_platform import AndroidRecorder
        rec = AndroidRecorder("/tmp", "TC-001")
        assert rec.get_segments() == []

    def test_ios_recorder_initial_state(self) -> None:
        from testagent.platform.ios_platform import iOSRecorder
        rec = iOSRecorder("/tmp", "TC-001")
        assert rec.get_segments() == []


class TestAbstractPlatform:
    def test_all_abstract_methods_implemented(self) -> None:
        import inspect
        from testagent.platform.interface import AbstractPlatform

        abstract_methods = [
            name for name, method in AbstractPlatform.__dict__.items()
            if inspect.isfunction(method) and getattr(method, "__isabstractmethod__", False)
        ]
        abstract_properties = [
            name for name, prop in AbstractPlatform.__dict__.items()
            if isinstance(prop, property) and getattr(prop.fget, "__isabstractmethod__", False)
        ]

        for platform_name in ("android", "ios"):
            platform = PlatformFactory.create(platform_name)
            for method in abstract_methods:
                assert hasattr(platform, method), f"{platform_name} is missing {method}"
            for prop_name in abstract_properties:
                assert hasattr(platform.__class__, prop_name)
                prop = getattr(platform.__class__, prop_name)
                assert prop is not None


def test_platform_consistency():
    android = PlatformFactory.create("android")
    ios = PlatformFactory.create("ios")
    assert android.platform_name in ("Android",)
    assert ios.platform_name in ("iOS",)
    assert android.automation_name in ("UiAutomator2",)
    assert ios.automation_name in ("XCUITest",)
