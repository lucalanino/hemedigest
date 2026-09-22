"""Smoke tests against the real Azure OpenAI deployment.

The only tests that cost money or need network. Deselected by default (see addopts
in pyproject.toml) and they skip, never fail, without a usable config.yaml or an
`az login` session -- so CI stays green with no Azure access.

    uv run pytest -m live

Four short calls covering the rules most likely to regress: the biopsy-only "<5%"
encoding, the cellularity enum, and the fibrosis range rule.
"""

from pathlib import Path

import pytest

from section_parser.parse_sections import build_client, load_config, parse_section
from section_parser.schemas import biopsy, cell_count

pytestmark = pytest.mark.live

CONFIG = Path(__file__).resolve().parent.parent / "section_parser" / "config.yaml"


@pytest.fixture(scope="module")
def az():
    if not CONFIG.exists():
        pytest.skip("no section_parser/config.yaml (copy config.yaml.example and fill it in)")
    try:
        return load_config(str(CONFIG))["azure_openai"]
    except SystemExit as exc:  # placeholders left in the file
        pytest.skip(f"config.yaml not usable: {exc}")


@pytest.fixture
async def client(az):
    try:
        # build_client fetches a token up front and raises SystemExit if it can't
        c = build_client(az)
    except SystemExit as exc:
        pytest.skip(f"no Azure credential available: {exc}")
    yield c
    await c.close()


async def parse(client, az, module, text):
    outcome = await parse_section(client, az["deployment"], module.PROMPT, text, module.SCHEMA, az["reasoning_effort"])
    assert not outcome.refused, "model refused the request"
    assert outcome.parsed is not None
    return outcome.parsed


async def test_cell_count_reads_the_differential(client, az):
    parsed = await parse(
        client,
        az,
        cell_count,
        "500-cell differential:\nBlasts 7%\nSegmented neutrophils 55%\nLymphocytes 20%\nMast cells 2%",
    )
    assert parsed.blasts_pct == 7
    assert parsed.mast_cells_pct == 2


async def test_biopsy_encodes_under_five_percent_as_three(client, az):
    """The one section where '<5%' means 3 rather than 0."""
    parsed = await parse(
        client,
        az,
        biopsy,
        "Core biopsy is adequate for evaluation. The marrow is normocellular at 45%. "
        "Blasts are not increased (<5%).",
    )
    assert parsed.blasts_pct == 3
    assert parsed.cellularity_pct == 45
    assert parsed.adequacy is True


async def test_cellularity_category_drops_qualifiers(client, az):
    """The Literal enum plus the 'strip qualifiers' instruction: no 'hypercellular for age'."""
    parsed = await parse(
        client,
        az,
        biopsy,
        "The marrow is markedly hypercellular for age at approximately 95%.",
    )
    assert parsed.cellularity_category == "hypercellular"
    assert parsed.cellularity_pct == 95


async def test_fibrosis_range_takes_the_larger_grade(client, az):
    parsed = await parse(
        client,
        az,
        biopsy,
        "Core biopsy is adequate. Increased reticulin fibrosis, MF-2 to MF-3.",
    )
    assert parsed.fibrosis_increased is True
    assert parsed.fibrosis_grade == 3
