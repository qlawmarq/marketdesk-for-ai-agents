"""Helper to load `.env` and apply values to OpenBB credentials.

Call at the top of every wrapper script:

    from _env import apply_to_openbb
    from openbb import obb
    apply_to_openbb()

The `.env` at the project root is then loaded automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)


# Environment variable name -> OpenBB credentials attribute name.
# When adding a provider, also update .env.example to stay in sync.
# (SEC_USER_AGENT is not a credentials attribute but an environment variable
#  that OpenBB reads directly, so load_dotenv is enough — no mapping needed.)
#
# Note on FRED_API_KEY:
#   OpenBB's `obb.economy.survey.*` (SLOOS / NY & Texas Fed / Michigan, etc.)
#   are structured wrappers built on top of FRED, and fred-api-ts cannot
#   reproduce the same structured output. macro_survey.py therefore uses
#   the FRED provider explicitly.
_CREDENTIAL_MAP = {
    "FMP_API_KEY": "fmp_api_key",
    "FRED_API_KEY": "fred_api_key",
    "EIA_API_KEY": "eia_api_key",
}


def apply_to_openbb() -> None:
    """Apply credentials from the current environment to OpenBB. Call after importing obb."""
    from openbb import obb

    for env_key, cred_attr in _CREDENTIAL_MAP.items():
        value = os.getenv(env_key)
        if not value:
            continue
        try:
            setattr(obb.user.credentials, cred_attr, value)
        except Exception:  # noqa: BLE001
            # Skip if the credentials attribute is not supported (e.g., provider not installed).
            pass
