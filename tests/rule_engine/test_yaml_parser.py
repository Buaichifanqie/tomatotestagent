from __future__ import annotations

import pytest
from testagent.rule_engine.yaml_parser import RuleYamlParser
from testagent.rule_engine.models import DataSourceConfig, AssertionConfig


class TestRuleYamlParser:
    def test_parse_setup_api(self):
        yaml_data = {
            "setup": [
                {
                    "name": "create_product",
                    "type": "api",
                    "method": "POST",
                    "endpoint": "${API_BASE}/products",
                    "body": {"name": "Test"},
                    "extract": {"product_id": "$.data.id"},
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_setup(yaml_data["setup"])
        assert len(result) == 1
        assert result[0].name == "create_product"
        assert result[0].type == "api"
        assert result[0].method == "POST"
        assert result[0].extract == {"product_id": "$.data.id"}

    def test_parse_setup_database(self):
        yaml_data = {
            "setup": [
                {
                    "name": "query_db",
                    "type": "database",
                    "connection": "sqlite:///test.db",
                    "query": "SELECT * FROM products WHERE id = :product_id",
                    "extract": {"db_price": "$.rows[0].price"},
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_setup(yaml_data["setup"])
        assert len(result) == 1
        assert result[0].type == "database"
        assert result[0].connection == "sqlite:///test.db"

    def test_parse_assertions_cross_source(self):
        yaml_data = {
            "assertions": [
                {
                    "type": "cross_source",
                    "field": "discount_price",
                    "sources": {
                        "ui": {"semantic": "商品折扣价"},
                        "api": {"source_ref": "create_product", "extract": "$.discount_price"},
                    },
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_assertions(yaml_data["assertions"])
        assert len(result) == 1
        assert result[0].type == "cross_source"
        assert result[0].field == "discount_price"

    def test_parse_assertions_ui_visible(self):
        yaml_data = {
            "assertions": [
                {
                    "type": "ui_visible",
                    "target": "商品卡片",
                    "expected": True,
                }
            ]
        }
        parser = RuleYamlParser()
        result = parser.parse_assertions(yaml_data["assertions"])
        assert len(result) == 1
        assert result[0].type == "ui_visible"

    def test_parse_empty_setup(self):
        parser = RuleYamlParser()
        result = parser.parse_setup([])
        assert result == []

    def test_parse_empty_assertions(self):
        parser = RuleYamlParser()
        result = parser.parse_assertions([])
        assert result == []
