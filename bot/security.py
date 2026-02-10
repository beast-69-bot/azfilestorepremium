from __future__ import annotations

import base64
import secrets


def new_code(nbytes: int = 16) -> str:
    # Unpredictable, URL-safe, no padding.
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode("ascii")


def new_token(nbytes: int = 18) -> str:
    # Token presented by users; keep it short-ish but high entropy.
    return new_code(nbytes=nbytes)

