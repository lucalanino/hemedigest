# TODO

Backlog of ideas to revisit. Not scheduled — captured so we don't lose them.

## Config design decisions (rationale, for when we expand config)

Conclusions from a design discussion — record so we don't re-litigate:

- **One file, not many.** Keep a single committed `config.yaml`. Splitting only pays
  off with separate audiences or lifecycles, and this is one CLI with one entry
  point. The only real axis (prod VM vs. local/shared) is already handled by env-var
  overrides for secrets/connection bits (placeholders committed, no secrets in code).
  If per-environment drift grows, add an **optional gitignored overlay**
  (`config.local.yaml`) merged on top at load — *not* topic-split files, and *not* a
  separate secrets file (env vars already fill that role).
- **What belongs in config vs. code.** Test: "does changing this change program
  *logic*, or just a *value*?" Values → config; logic → code.
  - Config-worthy: connection/runtime knobs (already there), plus
    `max_completion_tokens` (see below), maybe `log_level`.
  - Stay in code: **prompts and schemas** (version-controlled logic; the schema field
    list is coupled to `schema_signature`, the CSV columns, and `ID_COLUMNS` —
    externalizing creates a second source of truth and breaks checkpoint/CSV
    invariants), plus internal mechanics (`ID_COLUMNS`, token-estimate headroom, key
    separator).
- **Stay on YAML.** Human-edited, comments carry real explanation (api_version pin,
  reasoning-effort notes — rules out JSON), shallow nesting, already wired with
  `safe_load`. TOML is the only real alternative but buys nothing here. Watch the YAML
  "Norway problem" (`no`/`yes`/`on`/`off` and unquoted version strings coercing to
  bool/number) — keep quoting stringy scalars.
- **Validation upgrade (later, orthogonal to format):** as config grows, move
  validation into a **pydantic-settings** model that loads the YAML, applies env
  overrides, and type-checks in one place — replacing the hand-rolled checks in
  `load_config`. Complements YAML; hold off until the config is bigger.

## Config: expose `max_completion_tokens`

Make the model output cap configurable instead of relying on the SDK/model default.

- The README first-run tip already tells users to raise `max_completion_tokens` in
  `parse_section()` when a reasoning model spends its whole budget on reasoning and
  returns all-blank rows — but it's not currently a knob, so that means editing code.
- Add e.g. `azure_openai.max_completion_tokens` (nullable → omit the param to use the
  model default), thread it into the single `client.chat.completions.parse` call in
  `parse_section`.
- Doesn't affect the schema signature (output values, not field names), so no
  `--fresh` needed when changing it.
- Update the README tip to point at the config key instead of the code.

## Content-hash checkpointing

Make the checkpoint detect when a cell's **input text changed**, not just its
identity.

Today the checkpoint key is identity-based:
`(order_id, instance, section)` for instance sections, `order_id` for `final_dx`
(see `instance_key` / `final_dx_key` in `section_parser/parse_sections.py`). The
section text is **not** part of the key and is not fingerprinted. So if the report
text for an already-parsed cell changes but the ids stay the same, the run treats
it as done and skips it — only `--fresh` forces a re-parse.

**Idea:** fold a content hash of the section text into the key (or store it as a
separate field and compare on load), so changed text auto-reparses while unchanged
cells are still skipped.

**Cost (already assessed — negligible):**
- Runtime: hashing a few-KB section is microseconds, dwarfed by the rate-limited
  API call; cheaper than work the hot path already does (`estimate_tokens`, request
  serialization). No measurable impact even at 100k units.
- File size: fixed add per record — ~12 bytes truncated like the existing `sig`,
  ~64 bytes for full SHA-256 — on top of a record already dominated by the `result`
  payload. ~1–6 MB at 100k records. Noise.

**Design options:**
1. Fold hash into the key. Simplest; old records linger in the append-only file
   (deduped on load), bloat is the negligible amount above. *Leaning this way.*
2. Store hash as a separate field; `load_checkpoint` recomputes per-unit hashes and
   treats a mismatch as not-done. No stale keys, but needs the units passed into
   `load_checkpoint`.

**Note:** changes the schema-signature semantics slightly → do a one-time `--fresh`
run after adding it.

## Config: async vs. no-async execution mode

Expand `config.yaml` to choose between the current async fan-out and a simpler
synchronous (sequential) execution path.

- Add a config switch (e.g. `processing.mode: async | sync`, or an `async: true`
  flag) to select the execution strategy.
- `async` (current): `asyncio.gather` over work units with the `RateLimiter`
  (semaphore + sliding-window RPM/TPM) — fast, the default.
- `sync`: process units one at a time. Easier to debug, gentler on rate limits, no
  concurrency to reason about; useful for small runs, troubleshooting, or
  environments where the async path misbehaves (cf. the VM SDK-retry hang noted in
  the runtime config).
- Keep the checkpoint/resume, CSV assembly, and section-selection behavior identical
  across both modes — only the dispatch loop differs.
- Decide how `--concurrency` interacts with `sync` (ignore it, or error if both set).

## Config: selectable auth strategy

Make the Azure AD credential type configurable instead of hard-coding
`InteractiveBrowserCredential` in `build_client`.

- **Why now:** we use the interactive browser fallback only because the prod VM
  forces it (no API key, no other usable credential there). That's a deployment
  constraint, not the best experience for everyone else.
- **For sharing, add `DefaultAzureCredential` as the default/preferred option** — it
  walks a chain (env vars → managed identity → Azure CLI `az login` → etc.), so most
  collaborators authenticate with no browser pop-up and no code changes. Keep
  `InteractiveBrowserCredential` as the explicit opt-in for the VM.
- Add a config switch, e.g. `azure_openai.auth: default | browser` (default ->
  `DefaultAzureCredential`, browser -> `InteractiveBrowserCredential`), with an env
  override.
- `tenant_id` / `scope` stay as they are; both credential types accept them. Keep
  the token-provider flow (`get_bearer_token_provider`, no `api_key`) unchanged.
- Document the choice in the README auth section (when to use which).
