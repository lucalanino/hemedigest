"""Flow cytometry section schema"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class FlowSchema(BaseModel):
    """Structured fields extracted from the flow cytometry section."""

    blasts_pct: Optional[int] = Field(
        None,
        description=(
            "Blast percentage by flow cytometry (integer). Include blast equivalents. "
            "If a very small percentage is given (with or without a '<' sign, "
            "e.g. '<1%'), enter 0. If a range is given, use the larger value. "
            "Scope is myeloid neoplasms and ALL: do NOT count plasma cells, lymphoma "
            "lymphocytes, or solid-tumor cells as blasts."
        ),
    )
    adequacy: Optional[bool] = Field(
        None,
        description="True if the specimen is reported as adequate, False if inadequate.",
    )
    source: Optional[Literal["peripheral blood", "bone marrow", "other"]] = Field(
        None,
        description=(
            "Specimen source the flow study was run on: 'peripheral blood', "
            "'bone marrow', or 'other'."
        ),
    )
