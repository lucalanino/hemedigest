"""Prompt fragment shared by the blast-count sections (biopsy, aspirate, flow, cell_count, immunostains)."""

COMMON_POLICY = """\
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
