# TODO

Backlog of ideas to revisit. Not scheduled — captured so we don't lose them.

## Config design decisions (settled — don't re-litigate)

- **Single gitignored `config.yaml` + committed `config.yaml.example` template**
  (2026-07-06: replaced the earlier committed-`config.yaml`-plus-`config.local.yaml`-
  overlay design — deep merge was more indirection than the secret-protection problem
  needed). `config.yaml` is created by copying the example once; no merge step, no second
  source of truth for a given key. Not topic-split files, not a separate secrets file.
  Env-var overrides were removed earlier in favor of this file-based approach.
- **Config vs. code:** values → config (connection/runtime knobs, `log_level`); logic →
  code. Prompts and schemas stay in code — coupled to `schema_signature` / CSV columns /
  `ID_COLUMNS`, so externalizing them creates a second source of truth. Same for internal
  mechanics (token-estimate headroom, key separator, `EMPTY_SENTINELS`).
  `max_completion_tokens` deliberately not exposed — stick with the SDK default; edit
  `parse_section()` if you must.
- **Stay on YAML** (comments carry real explanation; shallow nesting). Watch the Norway
  problem — quote stringy scalars (`no`/`yes`/`on`/`off`, version strings).

## Concurrency & rate-limit tuning

Now observable via the end-of-run run report (logging shipped). Defaults:
`max_concurrency: 20`, `target_rpm: 2000` / `target_tpm: 200000`.

- **Raise `--concurrency` to find the real server ceiling.** At 20 the limiter never
  blocks (20 in-flight reasoning calls ≪ 2000 RPM), so the semaphore is the sole gate —
  which is also why progress comes in lockstep waves. The first sustained 429s mark the
  ceiling; that's when the `RateLimiter` starts earning its keep. The report's verdict
  says which limit is binding.
- **Consider honoring `Retry-After`** instead of the current fixed exponential backoff. A
  429 is rejected before processing (zero tokens billed), so the only cost is wasted
  wall-clock; the header is already logged.
