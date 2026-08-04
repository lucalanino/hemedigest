"""Final diagnosis section schema and prompt"""

from typing import Literal

from pydantic import BaseModel, Field

SECTION_NAME = "final_dx"

PROMPT = """\
You classify the FINAL DIAGNOSIS of a bone marrow pathology report, following the \
field descriptions in the schema.

Base the classification ONLY on the diagnosis rendered on the analyzed specimen. \
Prior history, prior diagnoses, and concurrent diagnoses at other sites must not \
change the category; if this specimen shows no morphologic disease, classify it as \
Negative even when the patient has a known malignancy."""

FinalDxCategory = Literal[
    "AML",
    "ALL",
    "MDS",
    "MPN",
    "CML",
    "MDS/MPN",
    "CMML",
    "Lymphoma",
    "Myeloma",
    "Solid",
    "Negative",
    "Other",
]

FinalDxStatus = Literal["overt", "residual", "remission", "negative"]


class FinalDxSchema(BaseModel):
    """Structured fields extracted from the report-level final diagnosis"""

    category: FinalDxCategory | None = Field(
        None,
        description=(
            "Single macro-category of the diagnosis rendered on the analyzed "
            "specimen. Choose the most specific applicable category: classify CML "
            "as CML and CMML as CMML rather than the broader MPN or MDS/MPN buckets. "
            "Use 'Negative' when the specimen shows no morphologic disease (normal, "
            "reactive, uninvolved, or in remission of a prior diagnosis). Use 'Other' "
            "if no category fits or it cannot be determined."
        ),
    )
    status: FinalDxStatus | None = Field(
        None,
        description=(
            "Disease status on the analyzed specimen, by the amount of detectable "
            "disease:\n"
            "- 'overt': frank/diagnostic disease is present; category is the entity.\n"
            "- 'residual': disease is detectable only as minimal/measurable residual "
            "disease; category is the entity.\n"
            "- 'remission': no detectable disease but a prior diagnosis is known; "
            "category is 'Negative'.\n"
            "- 'negative': no disease and no known prior diagnosis (normal, reactive, "
            "or uninvolved staging); category is 'Negative'."
        ),
    )


SCHEMA = FinalDxSchema
