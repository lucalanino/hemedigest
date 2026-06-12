"""Isolate whether the hang is in interactive auth (redirect delivery) or
the first HTTPS call to Azure. Run this ON THE VM, outside the parser.

    python auth_probe.py
"""

import os
import time

from azure.identity import InteractiveBrowserCredential

TENANT = os.environ.get("AZURE_TENANT_ID", "<YOUR_TENANT_ID>")
SCOPE = "https://cognitiveservices.azure.com/.default"

print("Requesting token (browser will open)...", flush=True)
t0 = time.time()
tok = InteractiveBrowserCredential(tenant_id=TENANT).get_token(SCOPE)
print(f"GOT TOKEN in {time.time() - t0:.1f}s; expires_on={tok.expires_on}", flush=True)
