# TODO

Backlog of ideas to revisit. Not scheduled — captured so we don't lose them.

## Config design decisions (rationale, for when we expand config)

Conclusions from a design discussion — record so we don't re-litigate:

- **One file, not many.** Keep a single committed `config.yaml`. Splitting only pays
  off with separate audiences or lifecycles, and this is one CLI with one entry
  point. The only real axis (prod VM vs. local/shared) is handled by the **optional
  gitignored overlay** `config.local.yaml`, deep-merged on top of `config.yaml` at
  load (placeholders committed, real values local — no secrets in code). *Done as of
  the auth change; env-var overrides were removed in its favor.* Keep it to this one
  overlay — *not* topic-split files, and *not* a separate secrets file.
- **What belongs in config vs. code.** Test: "does changing this change program
  *logic*, or just a *value*?" Values → config; logic → code.
  - Config-worthy: connection/runtime knobs (already there), maybe `log_level`.
    (`max_completion_tokens` was considered and **deliberately not exposed** — we
    stick with the SDK default; it's a manual code edit for anyone who wants to
    tune it, noted as such in the README.)
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

## Global dedup for instance text sections (measure first)

Instance keys are now content-addressed per `(order_id, instance)` (see `unit_key`),
but byte-identical text in *different* cells is still parsed separately. Consider a
**global** content key for the instance sections (`biopsy`, `aspirate`, `flow`,
`cell_count`, `immunostains`, `specimen_header`) so byte-identical text anywhere in
the file is parsed once and fanned out.

**Don't model this on `final_dx`.** `final_dx`'s content key (`order_id`,
`sha1(text)`) is *not* a general dedup feature — it exists only because `final_dx`
used to be **report-level** (one unit per `order_id`) and was recently made
per-instance (`738d964`) so consult instances can carry different diagnoses. The
content key just lets the common single-dx report collapse back to one unit per
report — i.e. it preserves the old report-level cost while allowing per-instance
divergence. It pays off because the dx is genuinely repeated verbatim across a
report's instances.

The instance sections have no such property: they're **per-specimen** — each
instance carries its own biopsy/aspirate text — so a within-`order_id` content key
would almost never collide. The only version with upside is a **global** content key
(no `order_id` at all), collapsing identical text *anywhere* in the file: canned
boilerplate, "SEE ABOVE" stubs, copy-pasted blocks, repeated `specimen_header`
outside-institution headers. It's lossless (identical input → identical output, so
fan-out is safe).

**Gate it on data.** Value depends entirely on how much exact-duplicate text exists,
and that's unknown. Before writing any code, run a one-off count of byte-identical
texts per section across `sections.jsonl` (as a % of total work units). Watch for
near-misses that aren't byte-identical (embedded newlines, trailing whitespace,
punctuation) — normalize only if it's clearly safe, since aggressive normalization
risks collapsing genuinely different specimens. If duplicates are <1–2%, skip it;
if `specimen_header`/boilerplate shows real repetition, add a global key for just
those sections.

**Cost:** hashing is negligible (~0.5s for 10k reports, measured). Content-hash
checkpointing has already landed, so keys are already content-derived — the
global-dedup change is mostly *dropping* the id from the key for the chosen sections.

**Migration:** this changes the key *format*, which `schema_signature` does not
fingerprint (it covers field names only). Content-hash checkpointing dodged this
because no checkpoint existed yet — but if a real production checkpoint exists by the
time this lands, decide between a sig-bump (fold a `KEY_SCHEME_VERSION` into
`schema_signature` to auto-purge old records with a warning) or a one-time `--fresh`.

## Revisit: concurrency, rate-limit ceiling, and 429 visibility

Picked up but deferred — currently running at `--concurrency 10` as-is. Discuss
again before tuning for throughput.

- **Bursty progress is structural, not throttling.** At concurrency 10 the
  `RateLimiter` (RPM 2500 / TPM 250000) effectively never blocks: 10 in-flight
  reasoning calls can't approach 2500 RPM (would need ~<0.24s/call). The sole gate
  is the `asyncio.Semaphore(10)`. `gather` starts the first ~10 together; homogeneous
  reasoning latency makes them finish together → slots free together → lockstep
  waves. That's the burst.
- **429s are mostly invisible today.** A 429 (`openai.RateLimitError`) is caught by
  the broad `except Exception` in `run_unit`, retried silently with exponential
  backoff, and only printed (`[failed, will retry on rerun]`) if it survives all
  `max_retries`. Transient 429s that succeed on retry produce **no** console output —
  so a quiet console only rules out *sustained* throttling, not occasional hits. The
  backoff sleep correctly runs outside the semaphore (slot released during wait).
- **No penalty for hitting limits.** A 429 is rejected before processing → zero
  tokens billed, no account-level consequence/escalation; resets over the sliding
  window. Response carries `Retry-After`. Only real cost is wasted wall-clock from
  retries/backoff. Note: current backoff is fixed exponential and does **not** read
  `Retry-After`.
- **To discuss / possible actions:**
  - Raise `--concurrency` (10 leaves the 2500 RPM ceiling largely unused; try 40–80)
    to find the real server ceiling — the first sustained 429s mark it, and that's
    when the `RateLimiter` finally earns its keep.
  - Make 429s observable: add a `tqdm.write` in the `except Exception` branch (log
    `type(exc).__name__`) on every retry, not just the final one.
  - Consider honoring `Retry-After` in the backoff instead of fixed exponential.
