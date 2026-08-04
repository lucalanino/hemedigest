"""Aspirate section schema and prompt"""

from pydantic import BaseModel, Field

from section_parser.schemas._common import COMMON_POLICY

SECTION_NAME = "aspirate"

PROMPT = f"""{COMMON_POLICY}

You are reading the BONE MARROW ASPIRATE smear section.
The differential blast count usually lives in the cell count section; treat any \
blast percentage here as a useful fallback. A very small blast percentage \
(with or without a "<" sign) should be encoded as 0."""


class AspirateSchema(BaseModel):
    """Structured fields extracted from the bone marrow aspirate section."""

    blasts_pct: int | None = Field(
        None,
        description=(
            "Blast percentage (integer). Include blast equivalents. "
            "If a very small percentage is given (with or without a '<' sign, "
            "e.g. '<1%' or 'rare'), enter 0. If a range is given, use the larger "
            "value. Scope is myeloid neoplasms and ALL: do NOT count plasma cells, "
            "lymphoma lymphocytes, or solid-tumor cells as blasts."
        ),
    )
    megakaryocytes_dysplastic: bool | None = Field(
        None,
        description="True if the megakaryocytic lineage is reported as dysplastic.",
    )
    erythroid_dysplastic: bool | None = Field(
        None,
        description="True if the erythroid lineage is reported as dysplastic.",
    )
    myeloid_dysplastic: bool | None = Field(
        None,
        description="True if the myeloid/granulocytic lineage is reported as dysplastic.",
    )
    ring_sideroblasts: bool | None = Field(
        None,
        description="True if ring sideroblasts are reported as present, False if explicitly absent.",
    )
    ring_sideroblasts_pct: int | None = Field(
        None,
        description=("Ring sideroblast percentage (integer). If a range is given, use the larger value."),
    )
    adequacy: bool | None = Field(
        None,
        description="True if the aspirate is reported as adequate, False if inadequate.",
    )


SCHEMA = AspirateSchema
