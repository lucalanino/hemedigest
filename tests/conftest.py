"""Shared fixtures for the section_parser test suite."""

import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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


def make_completion(parsed=None, refusal=None, tokens=100):
    """Fake OpenAI ChatCompletion-shaped object for parse_section to consume."""
    message = SimpleNamespace(refusal=refusal, parsed=parsed)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(total_tokens=tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def fake_client():
    client = SimpleNamespace()
    client.chat = SimpleNamespace(completions=SimpleNamespace(parse=AsyncMock()))
    client.close = AsyncMock()
    return client
