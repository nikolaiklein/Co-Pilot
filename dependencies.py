"""
FastAPI shared dependencies.

EC-2: All /cron/* and /debug/* endpoints must be protected by
      shared secret header check or IAM.
"""

import os
import logging
from typing import Annotated, Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# Owner user ID — only admin has access to /debug/* endpoints
ADMIN_USER_ID = 292628110


async def require_cron_secret(
    x_cron_secret: Annotated[Optional[str], Header(alias="X-Cron-Secret")] = None,
) -> None:
    """
    FastAPI dependency: validates X-Cron-Secret header.

    Raises 403 if:
    - CRON_SECRET env var is set and header is missing/wrong
    - CRON_SECRET env var is not configured (server misconfiguration)

    EC-2: required on all /cron/* and /debug/* routes.
    """
    expected = os.getenv("CRON_SECRET", "").strip()
    if not expected:
        # Misconfigured server — block all access until secret is set
        logger.error("CRON_SECRET env var is not configured — blocking cron endpoint access")
        raise HTTPException(status_code=403, detail="Forbidden")
    if x_cron_secret != expected:
        logger.warning("Cron/debug endpoint: invalid or missing X-Cron-Secret header")
        raise HTTPException(status_code=403, detail="Forbidden")
