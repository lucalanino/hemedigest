"""Aspirate (bone marrow aspirate smear) section schema."""

from typing import Optional

from pydantic import BaseModel, Field


class AspirateSchema(BaseModel):
    """Structured fields extracted from the bone marrow aspirate section."""

    blasts_pct: Optional[int] = Field(
        None,
        description=(
            "Blast percentage (integer). Include blast equivalents. "
            "If a very small percentage is given (with or without a '<' sign, "
            "e.g. '<1%' or 'rare'), enter 0. If a range is given, use the larger "
            "value. Scope is myeloid neoplasms and ALL: do NOT count plasma cells, "
            "lymphoma lymphocytes, or solid-tumor cells as blasts."
        ),
    )
    megakaryocytes_dysplastic: Optional[bool] = Field(
        None,
        description="True if the megakaryocytic lineage is reported as dysplastic.",
    )
    erythroid_dysplastic: Optional[bool] = Field(
        None,
        description="True if the erythroid lineage is reported as dysplastic.",
    )
    myeloid_dysplastic: Optional[bool] = Field(
        None,
        description="True if the myeloid/granulocytic lineage is reported as dysplastic.",
    )
    ring_sideroblasts: Optional[bool] = Field(
        None,
        description="True if ring sideroblasts are reported as present, False if explicitly absent.",
    )
    ring_sideroblasts_pct: Optional[int] = Field(
        None,
        description=(
            "Ring sideroblast percentage (integer). If a range is given, use the larger value."
        ),
    )
    adequacy: Optional[bool] = Field(
        None,
        description="True if the aspirate is reported as adequate, False if inadequate.",
    )
