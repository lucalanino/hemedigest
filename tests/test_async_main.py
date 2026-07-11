"""End-to-end happy-path test of async_main: config -> read -> units -> assemble -> CSV.
build_client (Azure auth) is monkeypatched out and chat.completions.parse is mocked --
no real network/API call is made.
"""

import argparse
import csv
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import yaml

from section_parser import parse_sections as ps
from section_parser.schemas.biopsy import SCHEMA as BIOPSY_SCHEMA

from tests.conftest import base_config_dict


def make_args(config_path):
    return argparse.Namespace(
        config=str(config_path),
        limit=None,
        fresh=False,
        concurrency=None,
        yes=True,
        log_level=None,
        log_file=None,
    )


async def test_async_main_happy_path_writes_csv_and_checkpoints(tmp_path, monkeypatch):
    input_path = tmp_path / "sections.jsonl"
    input_path.write_text(
        json.dumps({"order_id": "A1", "instance": 1, "biopsy": "text one"}) + "\n"
        + json.dumps({"order_id": "A2", "instance": 1, "biopsy": "text two"}) + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    checkpoint_path = tmp_path / "checkpoint.jsonl"
    cfg = base_config_dict()
    cfg["sections"] = ["biopsy"]
    cfg["files"]["input_jsonl"] = str(input_path)
    cfg["files"]["output_dir"] = str(out_dir)
    cfg["files"]["checkpoint"] = str(checkpoint_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    async def fake_parse(*, model, messages, response_format, reasoning_effort):
        text = messages[1]["content"]
        value = 60 if text == "text one" else 40
        message = SimpleNamespace(refusal=None, parsed=BIOPSY_SCHEMA(cellularity_pct=value))
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(total_tokens=100)
        return SimpleNamespace(choices=[choice], usage=usage)

    fake_client = SimpleNamespace()
    fake_client.chat = SimpleNamespace(
        completions=SimpleNamespace(parse=AsyncMock(side_effect=fake_parse))
    )
    fake_client.close = AsyncMock()
    monkeypatch.setattr(ps, "build_client", lambda az: fake_client)

    args = make_args(config_path)
    await ps.async_main(args)

    csv_files = list(out_dir.glob("parsed_sections_*.csv"))
    assert len(csv_files) == 1
    with open(csv_files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["order_id"] == "A1"
    assert rows[0]["biopsy_cellularity_pct"] == "60"
    assert rows[1]["order_id"] == "A2"
    assert rows[1]["biopsy_cellularity_pct"] == "40"

    assert checkpoint_path.exists()
    assert fake_client.chat.completions.parse.call_count == 2

    # second run: checkpoint should short-circuit reprocessing entirely
    await ps.async_main(make_args(config_path))
    assert fake_client.chat.completions.parse.call_count == 2  # no new calls made
