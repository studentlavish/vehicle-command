"""Sign in with Google — standard OAuth 2.0 Authorization Code flow with PKCE.

Setup (Google Cloud Console → APIs & Services → Credentials → OAuth client ID,
type "Web application"):
    Authorised redirect URI:  <PUBLIC_URL>/api/auth/google/callback
then set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and PUBLIC_URL (the dashboard's
https address).

Only accounts whose email already exists under Users can sign in — nothing is
created automatically.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
import jwt

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
FLOW_COOKIE = "g_oauth"
FLOW_SECONDS = 600


def client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def enabled() -> bool:
    return bool(client_id() and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip() and public_url())


def public_url() -> str:
    return os.environ.get("PUBLIC_URL", "").strip().rstrip("/")


def redirect_uri() -> str:
    return f"{public_url()}/api/auth/google/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_flow() -> Dict[str, str]:
    """Random state, nonce and PKCE verifier for one sign-in attempt."""
    return {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48)}


def authorization_url(flow: Dict[str, str]) -> str:
    challenge = _b64(hashlib.sha256(flow["verifier"].encode()).digest())
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })


async def exchange_code(code: str, verifier: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id(),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
    if r.status_code != 200:
        raise ValueError(f"token exchange failed ({r.status_code})")
    return r.json()


_jwks: Optional[jwt.PyJWKClient] = None


def verify_id_token(id_token: str, nonce: str) -> Dict[str, Any]:
    """Check Google's signature, audience, issuer, expiry and nonce. Returns the claims."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(CERTS_URL, cache_keys=True)
    key = _jwks.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(id_token, key.key, algorithms=["RS256"], audience=client_id(), issuer=ISSUERS)
    if claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    if not claims.get("email") or not claims.get("email_verified"):
        raise ValueError("Google account email is not verified")
    return claims


async def identity_from_code(code: str, flow: Dict[str, str]) -> Dict[str, Any]:
    """Code → verified identity claims (email, name, picture …)."""
    tokens = await exchange_code(code, flow["verifier"])
    if "id_token" not in tokens:
        raise ValueError("no id_token in Google's response")
    return verify_id_token(tokens["id_token"], flow["nonce"])
