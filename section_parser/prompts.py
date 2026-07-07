"""System prompts for each report section, paired with schemas in parse_sections.py."""

_COMMON_POLICY = """\
You extract structured data from a single section of a bone marrow pathology \
report. The clinical scope is MYELOID NEOPLASMS and ACUTE LYMPHOBLASTIC LEUKEMIA (ALL).

General rules:
- Only use information explicitly stated in the provided text. Do not infer or \
guess. If a field is not addressed, leave it null.
- For any numeric percentage, if a range is given use the LARGER value.
- "Blasts" means myeloblasts / lymphoblasts (and blast equivalents such as \
promonocytes and abnormal promyelocytes). Do NOT count plasma cells (myeloma), \
lymphoma lymphocytes, or solid-tumor cells as blasts.
- Follow each field's description precisely, including how to encode small or \
ranged blast percentages."""

BIOPSY_PROMPT = f"""{_COMMON_POLICY}

You are reading the BONE MARROW CORE BIOPSY (trephine) section.
Note: in this section only, a blast count reported as "<5%" should be encoded as 3."""

ASPIRATE_PROMPT = f"""{_COMMON_POLICY}

You are reading the BONE MARROW ASPIRATE smear section.
The differential blast count usually lives in the cell count section; treat any \
blast percentage here as a useful fallback. A very small blast percentage \
(with or without a "<" sign) should be encoded as 0."""

FLOW_PROMPT = f"""{_COMMON_POLICY}

You are reading the FLOW CYTOMETRY section. Capture the blast percentage by flow, \
whether the specimen was adequate, and the specimen source. A very small blast \
percentage (with or without a "<" sign) should be encoded as 0."""

CELL_COUNT_PROMPT = f"""{_COMMON_POLICY}

You are reading the ASPIRATE CELL COUNT (differential) section. This is the \
PRIMARY source for the blast count. A very small blast percentage (with or \
without a "<" sign) should be encoded as 0."""

IMMUNOSTAINS_PROMPT = f"""{_COMMON_POLICY}

You are reading the IMMUNOSTAINS section. Estimate the blast percentage, usually \
from a CD34 stain but stay open to other descriptions (e.g. CD117). A very small \
blast percentage (with or without a "<" sign) should be encoded as 0."""

SPECIMEN_HEADER_PROMPT = """\
You extract a single date from the header of an outside (consult) bone marrow \
specimen. The header is mostly boilerplate: a referring institution name, \
alphanumeric identifier/accession strings, and other clutter.

Your only job is to find the relevant date and return it as ISO 'YYYY-MM-DD':
- Source dates are written as mm/dd/yy or mm/dd/yyyy.
- If several dates appear, pick the FIRST one, which is usually next to the \
institution name and identifier strings.
- The plausible year range is about 1995-2026. Use it to repair obvious typos \
(e.g. '03/12/20150' -> '2015-03-12') and to resolve 2-digit years (95-99 -> \
1995-1999, 00-26 -> 2000-2026).
- If there is no plausible date, return null."""

FINAL_DX_PROMPT = """\
You classify the FINAL DIAGNOSIS of a bone marrow pathology report, following the \
field descriptions in the schema.

Base the classification ONLY on the diagnosis rendered on the analyzed specimen. \
Prior history, prior diagnoses, and concurrent diagnoses at other sites must not \
change the category; if this specimen shows no morphologic disease, classify it as \
Negative even when the patient has a known malignancy."""
