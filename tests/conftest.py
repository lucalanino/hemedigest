"""Shared fixtures for the section_parser test suite."""

import copy
from typing import Any

import pytest
import yaml


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def base_config_dict() -> dict[str, Any]:
    """A minimal, fully-valid config dict (mirrors config.yaml.example, no placeholders)."""
    return {
        "azure_openai": {
            "endpoint": "https://example-resource.openai.azure.com/",
            "deployment": "gpt-5.4",
            "api_version": "2024-12-01-preview",
            "scope": "https://cognitiveservices.azure.com/.default",
            "auth": "cli",
            "reasoning_effort": "low",
        },
        "files": {
            "input_jsonl": "data/sections.jsonl",
            "output_dir": "data",
            "output_prefix": "parsed_sections",
            "checkpoint": "data/.checkpoint.jsonl",
        },
        "sections": ["biopsy", "aspirate"],
        "processing": {},
    }


@pytest.fixture
def config_factory(tmp_path):
    """Returns a function that writes a config.yaml (base + overrides) and returns its path."""

    def _make(overrides: dict[str, Any] | None = None, filename: str = "config.yaml"):
        cfg = base_config_dict()
        if overrides:
            cfg = _deep_merge(cfg, overrides)
        path = tmp_path / filename
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    return _make


@pytest.fixture
def sample_rows() -> list[dict[str, Any]]:
    return [
        {
            "order_id": "A1",
            "instance": 1,
            "biopsy": "Cellularity 60%. Blasts 3%.",
            "aspirate": "Adequate aspirate, blasts <1%.",
        },
        {
            "order_id": "A2",
            "instance": 1,
            "biopsy": "Cellularity 40%. Blasts 2%.",
            "aspirate": None,
        },
    ]
