"""Unverified JWT claim extraction.

Dhan's signing key isn't available to this service, and engine/src/api/swing.rs's
parse_dhan_jwt_claims never checks the signature either -- this deliberately only
decodes claims for expiry/display purposes, it does not verify them.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DhanJwtClaims:
    exp: int
    iat: int
    dhan_client_id: str | None


class InvalidDhanTokenError(ValueError):
    pass


def parse_dhan_jwt_claims(token: str) -> DhanJwtClaims:
    parts = token.split(".")
    if len(parts) < 2:
        raise InvalidDhanTokenError("JWT payload segment is missing")

    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded))
        return DhanJwtClaims(
            exp=int(data["exp"]),
            iat=int(data["iat"]),
            dhan_client_id=data.get("dhanClientId"),
        )
    except Exception as exc:
        raise InvalidDhanTokenError(str(exc)) from exc
