"""Section registry: schema + prompt per section, keyed by section name.

To add a section: write a new module here defining SECTION_NAME, PROMPT, and
SCHEMA (see any existing module for the shape), then add it to _MODULES below.
Everything downstream (parsing, checkpointing, CSV columns) reads from SECTIONS.
"""

from pydantic import BaseModel

from section_parser.schemas import (
    aspirate,
    biopsy,
    cell_count,
    final_dx,
    flow,
    immunostains,
    specimen_header,
)

_MODULES = (biopsy, aspirate, flow, cell_count, immunostains, specimen_header, final_dx)

SECTIONS: dict[str, tuple[type[BaseModel], str]] = {
    m.SECTION_NAME: (m.SCHEMA, m.PROMPT) for m in _MODULES
}

__all__ = ["SECTIONS"]
