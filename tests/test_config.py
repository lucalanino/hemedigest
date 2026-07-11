import pytest
import yaml

from section_parser.parse_sections import ALL_SECTIONS, load_config

from tests.conftest import base_config_dict


def write_raw(tmp_path, cfg_dict, filename="config.yaml"):
    path = tmp_path / filename
    path.write_text(yaml.safe_dump(cfg_dict), encoding="utf-8")
    return path


# ---- missing file ----------------------------------------------------


def test_missing_config_file_raises_with_example_hint(tmp_path):
    missing = tmp_path / "config.yaml"
    with pytest.raises(SystemExit, match="config.yaml.example"):
        load_config(str(missing))


# ---- azure_openai required fields ----------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", ""),
        ("endpoint", "<YOUR_AZURE_OPENAI_ENDPOINT>"),
        ("deployment", ""),
        ("deployment", "<YOUR_DEPLOYMENT>"),
    ],
)
def test_missing_or_placeholder_required_field_raises(config_factory, field, value):
    path = config_factory({"azure_openai": {field: value}})
    with pytest.raises(SystemExit, match=field):
        load_config(str(path))


def test_auth_invalid_value_raises(config_factory):
    path = config_factory({"azure_openai": {"auth": "token"}})
    with pytest.raises(SystemExit, match="auth"):
        load_config(str(path))


def test_browser_auth_without_tenant_id_raises(config_factory):
    path = config_factory({"azure_openai": {"auth": "browser"}})
    with pytest.raises(SystemExit, match="tenant_id"):
        load_config(str(path))


def test_browser_auth_with_tenant_id_succeeds(config_factory):
    path = config_factory(
        {"azure_openai": {"auth": "browser", "tenant_id": "11111111-1111-1111-1111-111111111111"}}
    )
    config = load_config(str(path))
    assert config["azure_openai"]["auth"] == "browser"


def test_cli_auth_does_not_require_tenant_id(config_factory):
    path = config_factory({"azure_openai": {"auth": "cli"}})
    config = load_config(str(path))
    assert config["azure_openai"]["auth"] == "cli"


def test_invalid_reasoning_effort_raises(config_factory):
    path = config_factory({"azure_openai": {"reasoning_effort": "ultra"}})
    with pytest.raises(SystemExit, match="reasoning_effort"):
        load_config(str(path))


def test_reasoning_effort_defaults_to_low(tmp_path):
    cfg = base_config_dict()
    del cfg["azure_openai"]["reasoning_effort"]
    path = write_raw(tmp_path, cfg)
    config = load_config(str(path))
    assert config["azure_openai"]["reasoning_effort"] == "low"


# ---- sections ----------------------------------------------------


def test_sections_missing_raises(tmp_path):
    cfg = base_config_dict()
    del cfg["sections"]
    path = write_raw(tmp_path, cfg)
    with pytest.raises(SystemExit, match="sections"):
        load_config(str(path))


@pytest.mark.parametrize(
    "value,match",
    [
        ("biopsy", "list"),
        (["biopsy", "not_a_real_section"], "unknown"),
        ([], "empty"),
    ],
)
def test_sections_invalid_value_raises(config_factory, value, match):
    path = config_factory({"sections": value})
    with pytest.raises(SystemExit, match=match):
        load_config(str(path))


def test_sections_reordered_to_canonical_order(config_factory):
    path = config_factory({"sections": list(reversed(ALL_SECTIONS[:3]))})
    config = load_config(str(path))
    assert config["sections"] == ALL_SECTIONS[:3]


# ---- files ----------------------------------------------------


def test_files_not_a_mapping_raises(config_factory):
    path = config_factory({"files": ["not", "a", "mapping"]})
    with pytest.raises(SystemExit, match="files"):
        load_config(str(path))


def test_files_order_id_and_instance_col_default(config_factory):
    path = config_factory()
    config = load_config(str(path))
    assert config["files"]["order_id_col"] == "order_id"
    assert config["files"]["instance_col"] == "instance"


def test_files_order_id_col_override_respected(config_factory):
    path = config_factory({"files": {"order_id_col": "accession_number"}})
    config = load_config(str(path))
    assert config["files"]["order_id_col"] == "accession_number"


# ---- processing ----------------------------------------------------


def test_processing_defaults_applied_when_omitted(tmp_path):
    cfg = base_config_dict()
    del cfg["processing"]
    path = write_raw(tmp_path, cfg)
    config = load_config(str(path))
    proc = config["processing"]
    assert proc["max_concurrency"] == 20
    assert proc["target_rpm"] == 2000
    assert proc["target_tpm"] == 200000
    assert proc["max_retries"] == 5
    assert proc["retry_base_delay"] == 2.0
    assert proc["dedup"] is True
    assert proc["log_level"] == "WARNING"


def test_processing_not_a_mapping_raises(config_factory):
    path = config_factory({"processing": ["nope"]})
    with pytest.raises(SystemExit, match="processing"):
        load_config(str(path))


@pytest.mark.parametrize("field", ["max_concurrency", "target_rpm", "target_tpm"])
def test_processing_positive_int_fields_reject_non_positive(config_factory, field):
    path = config_factory({"processing": {field: 0}})
    with pytest.raises(SystemExit, match=field):
        load_config(str(path))


@pytest.mark.parametrize("field", ["max_concurrency", "target_rpm", "target_tpm"])
def test_processing_positive_int_fields_reject_non_int(config_factory, field):
    path = config_factory({"processing": {field: "twenty"}})
    with pytest.raises(SystemExit, match=field):
        load_config(str(path))


@pytest.mark.parametrize("field", ["max_concurrency", "target_rpm", "target_tpm"])
def test_processing_positive_int_fields_reject_bool(config_factory, field):
    path = config_factory({"processing": {field: True}})
    with pytest.raises(SystemExit, match=field):
        load_config(str(path))


def test_max_retries_negative_raises(config_factory):
    path = config_factory({"processing": {"max_retries": -1}})
    with pytest.raises(SystemExit, match="max_retries"):
        load_config(str(path))


def test_max_retries_zero_is_valid(config_factory):
    path = config_factory({"processing": {"max_retries": 0}})
    config = load_config(str(path))
    assert config["processing"]["max_retries"] == 0


def test_max_retries_rejects_bool(config_factory):
    path = config_factory({"processing": {"max_retries": False}})
    with pytest.raises(SystemExit, match="max_retries"):
        load_config(str(path))


def test_retry_base_delay_negative_raises(config_factory):
    path = config_factory({"processing": {"retry_base_delay": -0.5}})
    with pytest.raises(SystemExit, match="retry_base_delay"):
        load_config(str(path))


def test_retry_base_delay_accepts_int_and_float(config_factory):
    path = config_factory({"processing": {"retry_base_delay": 3}})
    config = load_config(str(path))
    assert config["processing"]["retry_base_delay"] == 3


def test_dedup_non_bool_raises(config_factory):
    path = config_factory({"processing": {"dedup": "yes"}})
    with pytest.raises(SystemExit, match="dedup"):
        load_config(str(path))


def test_log_level_invalid_raises(config_factory):
    path = config_factory({"processing": {"log_level": "INFO"}})
    with pytest.raises(SystemExit, match="log_level"):
        load_config(str(path))


def test_log_level_is_uppercased(config_factory):
    path = config_factory({"processing": {"log_level": "debug"}})
    config = load_config(str(path))
    assert config["processing"]["log_level"] == "DEBUG"
