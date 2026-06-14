from __future__ import annotations

import pytest

from testagent.db_toolkit.env import detect_environment
from testagent.db_toolkit.models import Environment


class TestDetectEnvironment:
    def test_url_test_keyword(self):
        env = detect_environment("mysql://u:p@host/test_db")
        assert env.level == Environment.TEST
        assert env.detected_by == "url_pattern"

    def test_url_staging_keyword(self):
        env = detect_environment("postgresql://u:p@host/staging_db")
        assert env.level == Environment.TEST

    def test_url_dev_keyword(self):
        env = detect_environment("mysql://u:p@host/dev_app")
        assert env.level == Environment.TEST

    def test_url_mock_keyword(self):
        env = detect_environment("sqlite:///mock_data.db")
        assert env.level == Environment.TEST

    def test_url_prod_keyword(self):
        env = detect_environment("mysql://u:p@host/prod_db")
        assert env.level == Environment.PRODUCTION
        assert env.detected_by == "url_pattern"

    def test_url_production_keyword(self):
        env = detect_environment("postgresql://u:p@host/production_db")
        assert env.level == Environment.PRODUCTION

    def test_url_no_keyword_defaults_prod(self):
        env = detect_environment("mysql://u:p@host/myapp")
        assert env.level == Environment.PRODUCTION
        assert env.detected_by == "default"

    def test_config_override_test(self):
        env = detect_environment("mysql://u:p@host/myapp", config_env="test")
        assert env.level == Environment.TEST
        assert env.detected_by == "config"

    def test_config_override_prod(self):
        env = detect_environment("mysql://u:p@host/test_db", config_env="production")
        assert env.level == Environment.PRODUCTION
        assert env.detected_by == "config"

    def test_case_insensitive_url(self):
        env = detect_environment("mysql://u:p@host/TEST_db")
        assert env.level == Environment.TEST

    def test_url_in_query_param_not_matched(self):
        env = detect_environment("mysql://u:p@host/myapp?timeout=10000")
        assert env.level == Environment.PRODUCTION

    def test_caching_same_url(self):
        from testagent.db_toolkit.env import clear_cache
        clear_cache()  # ensure clean state
        env1 = detect_environment("mysql://u:p@host/test_db")
        env2 = detect_environment("mysql://u:p@host/test_db")
        assert env1 is env2
