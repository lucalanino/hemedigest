"""One synchronous structured-output call, with retries OFF and a hard timeout.

Mirrors the parser's auth + config + schema exactly, but removes everything that
can mask a failure as a 'hang': no asyncio, no tqdm, no SDK retries, short
timeout. If the real call is failing, you'll see the actual error instead of a
frozen progress bar.

    python call_probe.py

Toggle the two suspect changes with env vars to bisect the regression:
    PROBE_API_VERSION=2024-12-01-preview   (old, known-good)   vs  preview
    PROBE_REASONING=off                    (omit the param)    vs  low
"""

import os
import time

from azure.identity import InteractiveBrowserCredential, get_bearer_token_provider
from openai import AzureOpenAI

from section_parser.parse_sections import load_config
from section_parser import prompts
from section_parser.schemas import FinalDxSchema

cfg = load_config(os.environ.get("PROBE_CONFIG", "section_parser/config.yaml"))
az = cfg["azure_openai"]

api_version = os.environ.get("PROBE_API_VERSION", az["api_version"])
reasoning = os.environ.get("PROBE_REASONING", az["reasoning_effort"])

print(f"endpoint   = {az['endpoint']}")
print(f"deployment = {az['deployment']}")
print(f"api_version= {api_version}")
print(f"reasoning  = {reasoning}")

print("\n[1] acquiring token (browser)...", flush=True)
t0 = time.time()
credential = InteractiveBrowserCredential(tenant_id=az["tenant_id"])
token_provider = get_bearer_token_provider(credential, az["scope"])
_ = token_provider()  # force interactive auth now, synchronously
print(f"    token OK in {time.time() - t0:.1f}s", flush=True)

# Retries OFF + short timeout so a stall surfaces fast instead of looking hung.
client = AzureOpenAI(
    azure_endpoint=az["endpoint"],
    azure_ad_token_provider=token_provider,
    api_version=api_version,
    max_retries=0,
    timeout=60.0,
)

kwargs = dict(
    model=az["deployment"],
    messages=[
        {"role": "system", "content": prompts.FINAL_DX_PROMPT},
        {"role": "user", "content": "Acute myeloid leukemia, NPM1-mutated."},
    ],
    response_format=FinalDxSchema,
)
if reasoning.lower() != "off":
    kwargs["reasoning_effort"] = reasoning

print("\n[2] one chat.completions.parse call...", flush=True)
t0 = time.time()
try:
    completion = client.chat.completions.parse(**kwargs)
    print(f"    call OK in {time.time() - t0:.1f}s", flush=True)
    print("    parsed =", completion.choices[0].message.parsed)
except Exception as exc:
    print(f"    FAILED after {time.time() - t0:.1f}s: {type(exc).__name__}: {exc}")
