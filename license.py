"""License key issuance + offline validation for Client Radar tiers.

Key format: CR-<TIERCODE>-<RANDOM12>-<SIG6>
  TIERCODE: S = starter ($49, 1 keyword), G = growth ($99, 3 keywords),
            X = scale ($199, 10 keywords)
  SIG6: first 6 hex chars of HMAC-SHA256(LICENSE_SECRET, "<TIERCODE>.<RANDOM12>")

Validation is offline (no server round-trip): recompute the HMAC and compare.
Gumroad purchase -> key delivery is handled by issue_key.py (CLI) or the
/webhook/gumroad endpoint (see README for the real webhook wiring).
"""
import hashlib
import hmac
import os
import secrets

SECRET = os.environ.get("LICENSE_SECRET", "dev-secret-change-me")

TIERS = {
    "starter": {"code": "S", "keywords": 1, "price": 49},
    "growth": {"code": "G", "keywords": 3, "price": 99},
    "scale": {"code": "X", "keywords": 10, "price": 199},
}
CODE_TO_TIER = {v["code"]: k for k, v in TIERS.items()}


def _sig(tier_code: str, rand: str) -> str:
    msg = f"{tier_code}.{rand}".encode()
    return hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()[:6].upper()


def issue_key(tier: str) -> str:
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier}")
    code = TIERS[tier]["code"]
    rand = secrets.token_hex(6).upper()  # 12 hex chars
    return f"CR-{code}-{rand}-{_sig(code, rand)}"


def validate_key(key: str):
    """Return tier name if valid, else None."""
    try:
        parts = key.strip().upper().split("-")
        if len(parts) != 4 or parts[0] != "CR":
            return None
        _, code, rand, sig = parts
        if code not in CODE_TO_TIER:
            return None
        if not hmac.compare_digest(_sig(code, rand), sig):
            return None
        return CODE_TO_TIER[code]
    except Exception:
        return None


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.strip().upper().encode()).hexdigest()[:16]
