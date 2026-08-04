"""Flow cytometry section schema and prompt"""

from typing import Literal

from pydantic import BaseModel, Field

from section_parser.schemas._common import COMMON_POLICY

SECTION_NAME = "flow"

PROMPT = f"""{COMMON_POLICY}

You are reading the FLOW CYTOMETRY section. Capture the blast percentage by flow, \
whether the specimen was adequate, and the specimen source. A very small blast \
percentage (with or without a "<" sign) should be encoded as 0."""


class FlowSchema(BaseModel):
    """Structured fields extracted from the flow cytometry section."""

    blasts_pct: int | None = Field(
        None,
        description=(
            "Blast percentage by flow cytometry (integer). Include blast equivalents. "
            "If a very small percentage is given (with or without a '<' sign, "
            "e.g. '<1%'), enter 0. If a range is given, use the larger value. "
            "Scope is myeloid neoplasms and ALL: do NOT count plasma cells, lymphoma "
            "lymphocytes, or solid-tumor cells as blasts."
        ),
    )
    adequacy: bool | None = Field(
        None,
        description="True if the specimen is reported as adequate, False if inadequate.",
    )
    source: Literal["peripheral blood", "bone marrow", "other"] | None = Field(
        None,
        description=("Specimen source the flow study was run on: 'peripheral blood', 'bone marrow', or 'other'."),
    )


SCHEMA = FlowSchema
