import base64
import json
import time

import pytest

from swing_atlas.repositories.dhan.jwt import InvalidDhanTokenError, parse_dhan_jwt_claims


def _make_token(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def test_parses_valid_claims() -> None:
    exp = int(time.time()) + 3600
    token = _make_token({"exp": exp, "iat": 1000, "dhanClientId": "CID123"})

    claims = parse_dhan_jwt_claims(token)

    assert claims.exp == exp
    assert claims.iat == 1000
    assert claims.dhan_client_id == "CID123"


def test_missing_client_id_claim_is_none() -> None:
    token = _make_token({"exp": 1, "iat": 0})

    claims = parse_dhan_jwt_claims(token)

    assert claims.dhan_client_id is None


def test_missing_payload_segment_raises() -> None:
    with pytest.raises(InvalidDhanTokenError):
        parse_dhan_jwt_claims("only-one-segment")


def test_malformed_base64_raises() -> None:
    with pytest.raises(InvalidDhanTokenError):
        parse_dhan_jwt_claims("header.not-valid-base64!!!.signature")


def test_missing_required_field_raises() -> None:
    token = _make_token({"iat": 0})  # no "exp"

    with pytest.raises(InvalidDhanTokenError):
        parse_dhan_jwt_claims(token)
