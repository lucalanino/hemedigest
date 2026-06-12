"""Aspirate cell count (differential) section schema."""

from typing import Optional

from pydantic import BaseModel, Field


class CellCountSchema(BaseModel):
    """Structured fields extracted from the aspirate cell count / differential section.

    This is the PRIMARY source for the blast count.
    """

    blasts_pct: Optional[int] = Field(
        None,
        description=(
            "Blast percentage from the differential (integer). This is the primary "
            "blast count. Include blast equivalents. If a very small percentage is "
            "given (with or without a '<' sign, e.g. '<1%'), enter 0. If a range is "
            "given, use the larger value. Scope is myeloid neoplasms and ALL: do NOT "
            "count plasma cells, lymphoma lymphocytes, or solid-tumor cells as blasts."
        ),
    )
    mast_cells_pct: Optional[int] = Field(
        None,
        description=(
            "Mast cell percentage from the differential (integer). Often not reported. "
            "If a range is given, use the larger value."
        ),
    )
