"""System prompts for each report section, paired with schemas in parse_sections.py.

Field-level rules live in the schema field descriptions; these prompts set the
overall task and cross-cutting policy.
"""

# Shared policy prepended to every instance-level section prompt.
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

FINAL_DX_PROMPT = """\
You classify the FINAL DIAGNOSIS of a bone marrow pathology report into a single \
macro-category. Choose exactly one of: AML, ALL, MDS, MPN, MDS/MPN, Lymphoma, \
Myeloma, Solid, Other.

Rules:
- Base the choice only on the diagnosis text provided.
- Pick the single best-fitting category for the overall/primary diagnosis.
- Use "Other" if it does not fit the listed categories or cannot be determined."""
