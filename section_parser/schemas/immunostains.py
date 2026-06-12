"""Immunostains section schema."""

from typing import Optional

from pydantic import BaseModel, Field


class ImmunostainsSchema(BaseModel):
    """Structured fields extracted from the immunostains section."""

    blasts_pct: Optional[int] = Field(
        None,
        description=(
            "Blast percentage estimated from immunostains (integer). This is usually "
            "based on a CD34 stain, but stay open to other descriptions (e.g. CD117). "
            "Include blast equivalents. If a very small percentage is given (with or "
            "without a '<' sign, e.g. '<1%'), enter 0. If a range is given, use the "
            "larger value. Scope is myeloid neoplasms and ALL: do NOT count plasma "
            "cells, lymphoma lymphocytes, or solid-tumor cells as blasts."
        ),
    )
