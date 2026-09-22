import pytest
from pydantic import BaseModel

from section_parser.parse_sections import (
    ALL_SECTIONS,
    _is_placeholder,
    is_empty_cell,
    schema_signature,
    section_fingerprint,
    selected_instance_sections,
    unit_key,
)
from section_parser.schemas.biopsy import SCHEMA as BIOPSY_SCHEMA

# ---- is_empty_cell ----------------------------------------------------


def test_is_empty_cell_none():
    assert is_empty_cell(None)


def test_is_empty_cell_blank_string():
    assert is_empty_cell("")
    assert is_empty_cell("   ")


def test_is_empty_cell_sentinels_case_insensitive():
    for value in ["na", "NA", "N/A", "none", "None", "nil", "null", "-", "--", "."]:
        assert is_empty_cell(value), value


def test_is_empty_cell_real_value_is_not_empty():
    assert not is_empty_cell("Cellularity 60%.")
    assert not is_empty_cell(0)  # a real, non-string value should not be treated as empty


# ---- _is_placeholder ----------------------------------------------------


def test_is_placeholder_falsy():
    assert _is_placeholder("")
    assert _is_placeholder(None)


def test_is_placeholder_angle_bracket():
    assert _is_placeholder("<YOUR_AZURE_OPENAI_ENDPOINT>")
    assert _is_placeholder("https://<resource>.openai.azure.com/")


def test_is_placeholder_real_value():
    assert not _is_placeholder("https://example-resource.openai.azure.com/")


# ---- selected_instance_sections ----------------------------------------------------


def test_selected_instance_sections_filters_and_preserves_canonical_order():
    # request in reverse order; result should follow ALL_SECTIONS' canonical order
    enabled = list(reversed(ALL_SECTIONS[:3]))
    result = selected_instance_sections(enabled)
    assert list(result) == ALL_SECTIONS[:3]


def test_selected_instance_sections_excludes_unlisted():
    result = selected_instance_sections([ALL_SECTIONS[0]])
    assert list(result) == [ALL_SECTIONS[0]]


# ---- schema_signature ----------------------------------------------------


def test_schema_signature_same_inputs_same_signature():
    a = schema_signature(True, "low", "gpt-5.4")
    b = schema_signature(True, "low", "gpt-5.4")
    assert a == b


@pytest.mark.parametrize(
    "args",
    [
        (False, "low", "gpt-5.4"),  # dedup differs
        (True, "high", "gpt-5.4"),  # reasoning_effort differs
        (True, "low", "gpt-4o"),  # deployment differs
    ],
)
def test_schema_signature_changes_when_any_arg_differs(args):
    a = schema_signature(True, "low", "gpt-5.4")
    b = schema_signature(*args)
    assert a != b


# ---- section_fingerprint ----------------------------------------------------


def test_section_fingerprint_changes_with_prompt_text():
    fp1 = section_fingerprint(BIOPSY_SCHEMA, "prompt one")
    fp2 = section_fingerprint(BIOPSY_SCHEMA, "prompt two")
    assert fp1 != fp2


def test_section_fingerprint_changes_with_schema_fields():
    class ModifiedBiopsySchema(BaseModel):
        cellularity_pct: int | None = None
        extra_field: str | None = None

    fp1 = section_fingerprint(BIOPSY_SCHEMA, "same prompt")
    fp2 = section_fingerprint(ModifiedBiopsySchema, "same prompt")
    assert fp1 != fp2


def test_section_fingerprint_stable_for_unrelated_sections():
    from section_parser.schemas.aspirate import SCHEMA as ASPIRATE_SCHEMA

    fp_before = section_fingerprint(ASPIRATE_SCHEMA, "aspirate prompt")
    # touching biopsy's fingerprint inputs shouldn't affect aspirate's
    section_fingerprint(BIOPSY_SCHEMA, "a different biopsy prompt")
    fp_after = section_fingerprint(ASPIRATE_SCHEMA, "aspirate prompt")
    assert fp_before == fp_after


# ---- unit_key ----------------------------------------------------


def test_unit_key_dedup_collapses_identical_text_across_rows():
    key_a = unit_key("order-1", 1, "biopsy", "identical text", dedup=True)
    key_b = unit_key("order-2", 1, "biopsy", "identical text", dedup=True)
    assert key_a == key_b


def test_unit_key_no_dedup_keeps_rows_distinct():
    key_a = unit_key("order-1", 1, "biopsy", "identical text", dedup=False)
    key_b = unit_key("order-2", 1, "biopsy", "identical text", dedup=False)
    assert key_a != key_b


def test_unit_key_different_text_differs_even_with_dedup():
    key_a = unit_key("order-1", 1, "biopsy", "text A", dedup=True)
    key_b = unit_key("order-1", 1, "biopsy", "text B", dedup=True)
    assert key_a != key_b


def test_unit_key_different_section_differs():
    key_a = unit_key("order-1", 1, "biopsy", "same text", dedup=True)
    key_b = unit_key("order-1", 1, "aspirate", "same text", dedup=True)
    assert key_a != key_b
