"""Specimen header section schema.

The consult specimen header is free-text boilerplate (referring institution name,
alphanumeric accession/identifier strings, and other clutter). The only field we
want from it is the date the outside specimen was collected/reported, normalized
to ISO format.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SpecimenHeaderSchema(BaseModel):
    """Date extracted from the consult specimen header.

    The date is typed as ``str`` (not ``datetime.date``) on purpose. A ``date``
    field would (1) render in the JSON schema as ``{"type": "string", "format":
    "date"}``, and strict structured outputs does not support ``format`` -- the
    API may reject the schema; and (2) make Pydantic *raise* on any non-ISO model
    output, which inside ``client.chat.completions.parse`` surfaces as an
    exception that burns the retry budget before settling on null. Keeping it a
    plain string + a soft ``field_validator`` gives the same "ISO date or null"
    result while staying API-compatible and degrading gracefully (validator
    returns None instead of raising).
    """

    date: Optional[str] = Field(
        None,
        description=(
            "The single most relevant date in the specimen header, normalized to ISO "
            "'YYYY-MM-DD'. Source dates appear as mm/dd/yy or mm/dd/yyyy, usually next "
            "to the referring institution name and alphanumeric identifier strings. If "
            "several dates are present, choose the FIRST one (nearest the institution "
            "name / identifier). The plausible year range is about 1995-2026: use it "
            "to repair obvious typos (e.g. '03/12/20150' -> '2015-03-12') and to "
            "resolve 2-digit years (95-99 -> 1995-1999, 00-26 -> 2000-2026). If no "
            "plausible date is present, leave null."
        ),
    )

    @field_validator("date")
    @classmethod
    def _coerce_iso(cls, v: Optional[str]) -> Optional[str]:
        # Defensive: drop anything that isn't a clean ISO date so a stray free-text
        # value never lands in the output column.
        if v is None:
            return None
        v = v.strip()
        return v if _ISO_DATE.match(v) else None
