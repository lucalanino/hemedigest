from section_parser.schemas import SECTIONS
from section_parser.schemas.specimen_header import SpecimenHeaderSchema


def test_sections_registry_has_all_modules():
    expected = {
        "biopsy",
        "aspirate",
        "flow",
        "cell_count",
        "immunostains",
        "specimen_header",
        "final_dx",
    }
    assert set(SECTIONS) == expected


def test_sections_registry_pairs_schema_and_prompt_correctly():
    for name, (schema, prompt) in SECTIONS.items():
        assert schema.__name__.lower().startswith(name.replace("_", ""))
        assert isinstance(prompt, str) and prompt.strip()


def test_coerce_iso_accepts_valid_date():
    obj = SpecimenHeaderSchema(date="2015-03-12")
    assert obj.date == "2015-03-12"


def test_coerce_iso_strips_whitespace():
    obj = SpecimenHeaderSchema(date="  2015-03-12  ")
    assert obj.date == "2015-03-12"


def test_coerce_iso_rejects_non_iso_format():
    obj = SpecimenHeaderSchema(date="03/12/2015")
    assert obj.date is None


def test_coerce_iso_rejects_garbage():
    obj = SpecimenHeaderSchema(date="not a date")
    assert obj.date is None


def test_coerce_iso_passes_through_none():
    obj = SpecimenHeaderSchema(date=None)
    assert obj.date is None
