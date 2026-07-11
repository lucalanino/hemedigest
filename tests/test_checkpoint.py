import logging

import pytest

from section_parser.parse_sections import CheckpointWriter, load_checkpoint


def test_load_checkpoint_missing_file_returns_empty(tmp_path):
    assert load_checkpoint(tmp_path / "nope.jsonl", "sig1") == {}


def test_load_checkpoint_reads_matching_records(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        '{"key": "a", "sig": "sig1", "result": {"x": 1}}\n'
        '{"key": "b", "sig": "sig1", "result": null}\n',
        encoding="utf-8",
    )
    done = load_checkpoint(path, "sig1")
    assert done == {"a": {"x": 1}, "b": None}


def test_load_checkpoint_skips_malformed_json_line(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        '{"key": "a", "sig": "sig1", "result": {"x": 1}}\n'
        "not valid json\n"
        '{"key": "b", "sig": "sig1", "result": {"x": 2}}\n',
        encoding="utf-8",
    )
    done = load_checkpoint(path, "sig1")
    assert done == {"a": {"x": 1}, "b": {"x": 2}}


def test_load_checkpoint_skips_record_missing_key(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text('{"sig": "sig1", "result": {"x": 1}}\n', encoding="utf-8")
    assert load_checkpoint(path, "sig1") == {}


def test_load_checkpoint_ignores_blank_lines(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text('\n  \n{"key": "a", "sig": "sig1", "result": {}}\n', encoding="utf-8")
    assert load_checkpoint(path, "sig1") == {"a": {}}


def test_load_checkpoint_drops_stale_sig_records(tmp_path, caplog):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text('{"key": "a", "sig": "old-sig", "result": {"x": 1}}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="section_parser"):
        done = load_checkpoint(path, "new-sig")
    assert done == {}
    assert any("1 checkpoint record" in r.message for r in caplog.records)


def test_load_checkpoint_stale_record_removes_earlier_valid_one_for_same_key(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    # same key written twice: once under the current sig, once (later) under a stale sig
    path.write_text(
        '{"key": "a", "sig": "new-sig", "result": {"x": 1}}\n'
        '{"key": "a", "sig": "old-sig", "result": {"x": 2}}\n',
        encoding="utf-8",
    )
    done = load_checkpoint(path, "new-sig")
    assert "a" not in done


async def test_checkpoint_writer_roundtrips_through_load_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    writer = CheckpointWriter(path, "sig1")
    await writer.write("key-a", {"x": 1})
    await writer.write("key-b", None)
    writer.close()

    done = load_checkpoint(path, "sig1")
    assert done == {"key-a": {"x": 1}, "key-b": None}


async def test_checkpoint_writer_reopened_on_existing_file_appends_not_truncates(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    first_writer = CheckpointWriter(path, "sig1")
    await first_writer.write("key-a", {"x": 1})
    first_writer.close()

    # simulate a resumed run: a new CheckpointWriter opened against the same,
    # already-populated file must not lose the prior writer's records
    second_writer = CheckpointWriter(path, "sig1")
    await second_writer.write("key-b", {"x": 2})
    second_writer.close()

    done = load_checkpoint(path, "sig1")
    assert done == {"key-a": {"x": 1}, "key-b": {"x": 2}}


async def test_checkpoint_writer_write_after_close_raises(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    writer = CheckpointWriter(path, "sig1")
    writer.close()
    with pytest.raises(ValueError):
        await writer.write("key-a", {})


def test_checkpoint_writer_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "checkpoint.jsonl"
    writer = CheckpointWriter(path, "sig1")
    writer.close()
    assert path.parent.exists()
