"""Biopsy section schema and prompt"""

from typing import Literal

from pydantic import BaseModel, Field

from section_parser.schemas._common import COMMON_POLICY

SECTION_NAME = "biopsy"

CellularityCategory = Literal["hypocellular", "normocellular", "hypercellular"]

PROMPT = f"""{COMMON_POLICY}

You are reading the BONE MARROW CORE BIOPSY (trephine) section.
Note: in this section only, a blast count reported as "<5%" should be encoded as 3."""


class BiopsySchema(BaseModel):
    """Structured fields extracted from the bone marrow core biopsy section."""

    cellularity_pct: int | None = Field(
        None,
        description=("Overall marrow cellularity as a percentage (0-100). If a range is given, use the LARGER value."),
    )
    cellularity_category: CellularityCategory | None = Field(
        None,
        description=(
            "Cellularity category stated in the report, reduced to exactly one of "
            "'hypocellular', 'normocellular', 'hypercellular'. Strip every qualifier: "
            "'markedly hypercellular for age' -> 'hypercellular', 'mildly hypocellular' "
            "-> 'hypocellular'. Take it from the wording only; do NOT infer it from the "
            "cellularity percentage. If the report gives no cellularity wording, leave null."
        ),
    )
    blasts_pct: int | None = Field(
        None,
        description=(
            "Blast percentage (integer). Include blast equivalents. "
            "In the BIOPSY section only: if reported as '<5%' enter 3. "
            "If a range is given, use the larger value. Blasts may be described "
            "in many ways. Scope is myeloid neoplasms and ALL: do NOT count "
            "plasma cells, lymphoma lymphocytes, or solid-tumor cells as blasts."
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
    fibrosis_increased: bool | None = Field(
        None,
        description=(
            "True if marrow fibrosis is reported as increased, irrespective of any grade. "
            "If a grade is given and it is MF-1, MF-2 or MF-3, then this is TRUE. "
            "If a grade of MF-0 is given, then this is FALSE."
        ),
    )
    fibrosis_grade: int | None = Field(
        None,
        description=(
            "Reticulin fibrosis grade per MF scale (0-3). Report ONLY if an explicit "
            "MF grade is stated. Do NOT infer a grade from adjectives such as "
            "'moderate' or 'severe'. If two grades are given, whatever the separator "
            "('MF-2 to MF-3', 'MF 2-3', 'MF-2/3', 'grade 2-3'), use the LARGER value."
        ),
    )
    adequacy: bool | None = Field(
        None,
        description="True if the specimen is reported as adequate, False if inadequate.",
    )


SCHEMA = BiopsySchema
