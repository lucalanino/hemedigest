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

## Logging & observability (design first — gates concurrency tuning)

Settle *what* the run surfaces and *how* before tuning throughput, because the
concurrency decision depends on signals we don't currently emit. Discussion item,
not yet implemented.

**Why this comes first — 429s are nearly invisible today.** A 429
(`openai.RateLimitError`) is caught by the broad `except Exception` in `run_unit`,
retried silently with exponential backoff, and only printed
(`[failed, will retry on rerun]`) if it survives all `max_retries`. Transient 429s
that succeed on retry produce **no** output, so a quiet console only rules out
*sustained* throttling, not occasional hits. You can't tune concurrency you can't
observe.

**What to surface (to discuss):**
- **Retry/429 events** — log `type(exc).__name__` on every retry (a `tqdm.write` in
  the `except` branch), not just the final failure, so occasional throttling shows.
- **Rate-limit utilization** — actual RPM/TPM vs. the configured ceilings
  (`target_rpm` / `target_tpm`), and whether the `RateLimiter` or the
  `asyncio.Semaphore` is the binding constraint. This is the signal that says whether
  raising `--concurrency` will actually help.
- **Throughput** — units/sec, ETA, rolling per-call latency.
- **Failures** — an end-of-run summary of permanently-failed units and why.

**Levels:** consider a `log_level` knob (cf. config-design notes) or
quiet/normal/verbose — default normal keeps the current tqdm bar + summary; verbose
adds per-retry and utilization lines; quiet for batch/VM runs.

## Concurrency & rate-limit tuning (after logging)

Depends on the logging above: once 429s and utilization are observable, tune for
throughput. Currently `--concurrency 20` (config `max_concurrency: 20`).

- **Bursty progress is structural, not throttling.** At concurrency 20 the
  `RateLimiter` (RPM 2000 / TPM 200000) effectively never blocks: 20 in-flight
  reasoning calls can't approach 2000 RPM (would need ~<0.6s/call). The sole gate is
  the `asyncio.Semaphore(20)`. `gather` starts the first ~20 together; homogeneous
  reasoning latency makes them finish together → slots free together → lockstep
  waves. That's the burst.
- **No penalty for hitting limits.** A 429 is rejected before processing → zero
  tokens billed, no account-level consequence; resets over the sliding window.
  Response carries `Retry-After`. Only real cost is wasted wall-clock from
  retries/backoff. Current backoff is fixed exponential and does **not** read
  `Retry-After`.
- **To discuss / possible actions (once observable):**
  - Raise `--concurrency` (20 leaves the 2000 RPM ceiling largely unused) to find the
    real server ceiling — the first sustained 429s mark it, and that's when the
    `RateLimiter` finally earns its keep.
  - Consider honoring `Retry-After` in the backoff instead of fixed exponential.
