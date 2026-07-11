"""Immunostains section schema and prompt"""

from typing import Optional

from pydantic import BaseModel, Field

from section_parser.schemas._common import COMMON_POLICY

SECTION_NAME = "immunostains"

PROMPT = f"""{COMMON_POLICY}

You are reading the IMMUNOSTAINS section. Estimate the blast percentage, usually \
from a CD34 stain but stay open to other descriptions (e.g. CD117). A very small \
blast percentage (with or without a "<" sign) should be encoded as 0."""


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


SCHEMA = ImmunostainsSchema
