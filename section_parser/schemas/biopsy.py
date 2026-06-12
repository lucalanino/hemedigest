"""Biopsy (trephine core) section schema."""

from typing import Optional

from pydantic import BaseModel, Field


class BiopsySchema(BaseModel):
    """Structured fields extracted from the bone marrow core biopsy section."""

    cellularity_pct: Optional[int] = Field(
        None,
        description=(
            "Overall marrow cellularity as a percentage (0-100). "
            "If a range is given, use the LARGER value."
        ),
    )
    cellularity_category: Optional[str] = Field(
        None,
        description=(
            "Cellularity category exactly as stated in the report "
            "(e.g. 'hypocellular', 'normocellular', 'hypercellular'). "
            "Read it verbatim; do NOT infer it from the percentage."
        ),
    )
    blasts_pct: Optional[int] = Field(
        None,
        description=(
            "Blast percentage (integer). Include blast equivalents. "
            "In the BIOPSY section only: if reported as '<5%' enter 3. "
            "If a range is given, use the larger value. Blasts may be described "
            "in many ways. Scope is myeloid neoplasms and ALL: do NOT count "
            "plasma cells, lymphoma lymphocytes, or solid-tumor cells as blasts."
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
    fibrosis_increased: Optional[bool] = Field(
        None,
        description="True if marrow fibrosis is reported as increased, irrespective of any grade.",
    )
    fibrosis_grade: Optional[int] = Field(
        None,
        description=(
            "Reticulin fibrosis grade per MF scale (0-3). Report ONLY if an explicit "
            "MF grade is stated. Do NOT infer a grade from adjectives such as "
            "'moderate' or 'severe'."
        ),
    )
    adequacy: Optional[bool] = Field(
        None,
        description="True if the specimen is reported as adequate, False if inadequate.",
    )
