import csv
import logging

import pytest

from section_parser.parse_sections import (
    build_fieldnames,
    check_input_columns,
    passthrough_columns,
    read_jsonl,
    write_csv,
)


# ---- passthrough_columns ----------------------------------------------------


def test_passthrough_columns_excludes_known_sections():
    rows = [{"order_id": "A1", "biopsy": "text", "mrn": "123"}]
    assert passthrough_columns(rows) == ["order_id", "mrn"]


def test_passthrough_columns_preserves_first_seen_order_across_rows():
    rows = [
        {"order_id": "A1", "biopsy": "x"},
        {"mrn": "123", "order_id": "A1", "extra": "y"},
    ]
    assert passthrough_columns(rows) == ["order_id", "mrn", "extra"]


def test_passthrough_columns_empty_rows():
    assert passthrough_columns([]) == []


# ---- build_fieldnames ----------------------------------------------------


def test_build_fieldnames_passthrough_first_then_section_fields():
    fieldnames = build_fieldnames(["order_id"], ["biopsy"])
    assert fieldnames[0] == "order_id"
    assert "biopsy_cellularity_pct" in fieldnames
    assert "biopsy_blasts_pct" in fieldnames
    assert fieldnames.index("order_id") < fieldnames.index("biopsy_cellularity_pct")


def test_build_fieldnames_multiple_sections_in_canonical_order():
    fieldnames = build_fieldnames([], ["aspirate", "biopsy"])
    # canonical order is biopsy before aspirate regardless of the `enabled` list order
    assert fieldnames.index("biopsy_blasts_pct") < fieldnames.index("aspirate_blasts_pct")


# ---- write_csv ----------------------------------------------------


def test_write_csv_writes_header_and_rows(tmp_path):
    out_path = tmp_path / "out.csv"
    out_rows = [{"order_id": "A1", "biopsy_cellularity_pct": 60}]
    write_csv(out_rows, out_path, ["biopsy"], ["order_id"])

    with open(out_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames[0] == "order_id"
    assert rows[0]["order_id"] == "A1"
    assert rows[0]["biopsy_cellularity_pct"] == "60"


def test_write_csv_ignores_extra_keys_not_in_fieldnames(tmp_path):
    out_path = tmp_path / "out.csv"
    out_rows = [{"order_id": "A1", "biopsy_cellularity_pct": 60, "unexpected_key": "oops"}]
    write_csv(out_rows, out_path, ["biopsy"], ["order_id"])

    with open(out_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "unexpected_key" not in reader.fieldnames


def test_write_csv_creates_parent_directories(tmp_path):
    out_path = tmp_path / "nested" / "dir" / "out.csv"
    write_csv([], out_path, ["biopsy"], ["order_id"])
    assert out_path.exists()


# ---- read_jsonl ----------------------------------------------------


def test_read_jsonl_parses_valid_lines(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(str(path)) == [{"a": 1}, {"b": 2}]


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "in.jsonl"
    path.write_text('{"a": 1}\n\n  \n{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(str(path)) == [{"a": 1}, {"b": 2}]


def test_read_jsonl_skips_malformed_line_and_logs(tmp_path, caplog):
    path = tmp_path / "in.jsonl"
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="section_parser"):
        rows = read_jsonl(str(path))
    assert rows == [{"a": 1}, {"b": 2}]
    assert any("malformed" in r.message for r in caplog.records)


# ---- check_input_columns ----------------------------------------------------


def test_check_input_columns_raises_on_missing_section_column():
    rows = [{"order_id": "A1", "instance": 1, "biopsy": "x"}]
    with pytest.raises(SystemExit, match="aspirate"):
        check_input_columns(rows, ["biopsy", "aspirate"], "order_id", "instance")


def test_check_input_columns_passes_when_all_present():
    rows = [{"order_id": "A1", "instance": 1, "biopsy": "x", "aspirate": "y"}]
    check_input_columns(rows, ["biopsy", "aspirate"], "order_id", "instance")  # no raise


def test_check_input_columns_warns_on_ambiguous_repeated_order_id(caplog):
    rows = [
        {"order_id": "A1", "biopsy": "x"},
        {"order_id": "A1", "biopsy": "y"},
    ]
    with caplog.at_level(logging.WARNING, logger="section_parser"):
        check_input_columns(rows, ["biopsy"], "order_id", "instance")
    assert any("instance" in r.message for r in caplog.records)


def test_check_input_columns_no_warning_without_repeats(caplog):
    rows = [
        {"order_id": "A1", "biopsy": "x"},
        {"order_id": "A2", "biopsy": "y"},
    ]
    with caplog.at_level(logging.WARNING, logger="section_parser"):
        check_input_columns(rows, ["biopsy"], "order_id", "instance")
    assert caplog.records == []


def test_check_input_columns_empty_rows_is_noop():
    check_input_columns([], ["biopsy"], "order_id", "instance")  # no raise
