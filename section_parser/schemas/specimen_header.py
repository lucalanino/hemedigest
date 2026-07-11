"""Specimen header section schema and prompt: extracts the date from an otherwise boilerplate free-text header."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SECTION_NAME = "specimen_header"

PROMPT = """\
You extract a single date from a bone marrow specimen header. The header is \
mostly boilerplate: an institution name, alphanumeric identifier/accession \
strings, and other clutter.

Your only job is to find the relevant date and return it as ISO 'YYYY-MM-DD':
- Source dates are written as mm/dd/yy or mm/dd/yyyy.
- If several dates appear, pick the FIRST one, which is usually next to the \
institution name and identifier strings.
- The plausible year range is about 1995-2030. Use it to repair obvious typos \
(e.g. '03/12/20150' -> '2015-03-12') and to resolve 2-digit years (95-99 -> \
1995-1999, 00-30 -> 2000-2030).
- If there is no plausible date, return null."""


class SpecimenHeaderSchema(BaseModel):
    """Date extracted from the specimen header, kept as ``str`` since structured outputs rejects ``format`` and a ``date`` field would raise (not null) on bad model output."""

    date: Optional[str] = Field(
        None,
        description=(
            "The single most relevant date in the specimen header, normalized to ISO "
            "'YYYY-MM-DD'. Source dates appear as mm/dd/yy or mm/dd/yyyy, usually next "
            "to the institution name and alphanumeric identifier strings. If "
            "several dates are present, choose the FIRST one (nearest the institution "
            "name / identifier). The plausible year range is about 1995-2030: use it "
            "to repair obvious typos (e.g. '03/12/20150' -> '2015-03-12') and to "
            "resolve 2-digit years (95-99 -> 1995-1999, 00-30 -> 2000-2030). If no "
            "plausible date is present, leave null."
        ),
    )

    @field_validator("date")
    @classmethod
    def _coerce_iso(cls, v: Optional[str]) -> Optional[str]:
        # drop anything that isn't a clean ISO date
        if v is None:
            return None
        v = v.strip()
        return v if _ISO_DATE.match(v) else None


SCHEMA = SpecimenHeaderSchema
