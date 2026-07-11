"""build_work_units and assemble_rows are tested together (not just in isolation) so a
lookup-key mismatch between the two -- which the source explicitly warns is fragile --
would actually surface as a test failure.
"""

from section_parser.parse_sections import assemble_rows, build_work_units


def test_empty_and_placeholder_cells_are_skipped_in_both_stages():
    rows = [
        {"order_id": "A1", "instance": 1, "biopsy": "", "aspirate": "NA"},
        {"order_id": "A2", "instance": 1, "biopsy": None, "aspirate": "real text"},
    ]
    units, n_dup, n_skipped_empty = build_work_units(
        rows, ["biopsy", "aspirate"], dedup=True, order_id_col="order_id", instance_col="instance"
    )
    # only row A2's aspirate has real content
    assert len(units) == 1
    assert units[0].text == "real text"
    # "" and None are blank, don't count; "NA" is a placeholder and does count
    assert n_skipped_empty == 1

    results = {units[0].key: {"blasts_pct": 5}}
    out_rows = assemble_rows(
        rows, results, ["biopsy", "aspirate"], dedup=True, passthrough=["order_id"],
        order_id_col="order_id", instance_col="instance",
    )
    assert "biopsy_blasts_pct" not in out_rows[0]
    assert "aspirate_blasts_pct" not in out_rows[0]
    assert out_rows[1]["aspirate_blasts_pct"] == 5
    assert "biopsy_blasts_pct" not in out_rows[1]


def test_dedup_true_collapses_identical_text_and_both_rows_pick_up_same_result():
    rows = [
        {"order_id": "A1", "instance": 1, "biopsy": "identical text"},
        {"order_id": "A2", "instance": 1, "biopsy": "identical text"},
    ]
    units, n_dup, n_skipped_empty = build_work_units(
        rows, ["biopsy"], dedup=True, order_id_col="order_id", instance_col="instance"
    )
    assert len(units) == 1
    assert n_dup == 1

    results = {units[0].key: {"cellularity_pct": 60}}
    out_rows = assemble_rows(
        rows, results, ["biopsy"], dedup=True, passthrough=["order_id"],
        order_id_col="order_id", instance_col="instance",
    )
    assert out_rows[0]["biopsy_cellularity_pct"] == 60
    assert out_rows[1]["biopsy_cellularity_pct"] == 60


def test_dedup_false_keeps_rows_distinct_with_independent_results():
    rows = [
        {"order_id": "A1", "instance": 1, "biopsy": "identical text"},
        {"order_id": "A2", "instance": 1, "biopsy": "identical text"},
    ]
    units, n_dup, n_skipped_empty = build_work_units(
        rows, ["biopsy"], dedup=False, order_id_col="order_id", instance_col="instance"
    )
    assert len(units) == 2
    assert n_dup == 0

    results = {
        units[0].key: {"cellularity_pct": 60},
        units[1].key: {"cellularity_pct": 40},
    }
    out_rows = assemble_rows(
        rows, results, ["biopsy"], dedup=False, passthrough=["order_id"],
        order_id_col="order_id", instance_col="instance",
    )
    assert out_rows[0]["biopsy_cellularity_pct"] == 60
    assert out_rows[1]["biopsy_cellularity_pct"] == 40


def test_full_round_trip_multiple_sections_and_fields():
    rows = [
        {"order_id": "A1", "instance": 1, "biopsy": "bx text", "aspirate": "asp text"},
    ]
    units, _, _ = build_work_units(
        rows, ["biopsy", "aspirate"], dedup=True, order_id_col="order_id", instance_col="instance"
    )
    by_text = {u.text: u for u in units}
    results = {
        by_text["bx text"].key: {"cellularity_pct": 70, "blasts_pct": 3},
        by_text["asp text"].key: {"blasts_pct": 0},
    }
    out_rows = assemble_rows(
        rows, results, ["biopsy", "aspirate"], dedup=True, passthrough=["order_id"],
        order_id_col="order_id", instance_col="instance",
    )
    row = out_rows[0]
    assert row["order_id"] == "A1"
    assert row["biopsy_cellularity_pct"] == 70
    assert row["biopsy_blasts_pct"] == 3
    assert row["aspirate_blasts_pct"] == 0


def test_assemble_rows_skips_missing_result_for_unit_not_yet_processed():
    rows = [{"order_id": "A1", "instance": 1, "biopsy": "bx text"}]
    out_rows = assemble_rows(
        rows, {}, ["biopsy"], dedup=True, passthrough=["order_id"],
        order_id_col="order_id", instance_col="instance",
    )
    assert "biopsy_cellularity_pct" not in out_rows[0]
    assert out_rows[0]["order_id"] == "A1"
