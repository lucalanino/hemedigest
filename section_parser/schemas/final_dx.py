"""Final diagnosis section schema (report-level)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

FinalDxCategory = Literal[
    "AML",
    "ALL",
    "MDS",
    "MPN",
    "MDS/MPN",
    "Lymphoma",
    "Myeloma",
    "Solid",
    "Other",
]


class FinalDxSchema(BaseModel):
    """Structured fields extracted from the report-level final diagnosis.

    This is report-level (one per order_id), not per-instance.
    """

    category: Optional[FinalDxCategory] = Field(
        None,
        description=(
            "The single best macro-category for the final diagnosis. Choose exactly "
            "one of: AML, ALL, MDS, MPN, MDS/MPN, Lymphoma, Myeloma, Solid, Other. "
            "Use 'Other' if it does not fit the listed categories or cannot be "
            "determined."
        ),
    )
