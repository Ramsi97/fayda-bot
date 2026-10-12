# core/api/fayda_client.py
"""
Async Fayda eSignet PKCE + OTP client.

Mirrors fayda_flow.sh step-by-step:
  1. GET  /authorize                           → seed session cookies
  2. GET  /v1/esignet/csrf/token               → CSRF token
  3. POST /v1/esignet/authorization/v2/oauth-details  → transactionId + hashes
  4. POST /v1/esignet/authorization/send-otp   → triggers OTP to user's phone
  5. POST /v1/esignet/authorization/v2/authenticate  → verify OTP
  6. POST /v1/esignet/authorization/auth-code  → auth code
  7. POST api-resident/esignet/exchangeAutheCode → JWT
  8. POST api-resident/esignet/exchangeResident  → resident profile (name, photo …)
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import httpx


# ── Exceptions ────────────────────────────────────────────────────────────────
class FaydaConnectionError(Exception):
    """Raised when a Fayda server could not be reached after retries.

    Distinct from ValueError (wrong OTP / auth rejection) so callers can show a
    friendly "servers temporarily down" message instead of a raw traceback.
    """


# ── Constants ─────────────────────────────────────────────────────────────────
AUTH_BASE       = "https://auth.fayda.et"
RESIDENT_BASE   = "https://api-resident.fayda.et"
CLIENT_ID       = "ajcCvwQcVm1dAr6HaW4Y5ObnDmwPPTpH9oDaZSPPrpo"
REDIRECT_URI    = "https://resident.fayda.et/callback"
SCOPE           = "openid profile email"
ACR             = "mosip:idp:acr:generated-code"

COMMON_HEADERS = {
    "Origin":  "https://resident.fayda.et",
    "Referer": "https://resident.fayda.et/",
    "User-Agent": "Mozilla/5.0 (compatible; FaydaBot/1.0)",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))[:43]
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _od_hash(response_obj: dict) -> str:
    """SHA-256 base64url of the raw JSON response object (compact, no trailing newline)."""
    raw = json.dumps(response_obj, separators=(",", ":"), ensure_ascii=False)
    return _b64url(hashlib.sha256(raw.encode()).digest())


def _format_id(digits: str) -> str:
    """Group a numeric id into space-separated blocks of 4.

    390712813067       -> '3907 1281 3067'   (12-digit FIN)
    3076328173524539   -> '3076 3281 7352 4539' (16-digit FAN)
    Non-12/16-digit input is returned unchanged.
    """
    d = (digits or "").replace(" ", "").strip()
    if d.isdigit() and len(d) in (12, 16):
        return " ".join(d[i:i + 4] for i in range(0, len(d), 4))
    return digits


def _greg_to_eth(greg: str) -> str:
    """Convert a Gregorian 'YYYY/MM/DD' (or 'YYYY-MM-DD') date to the Ethiopian
    calendar, formatted 'DD/MM/YYYY'. Returns '' on any parse failure.

    Standalone (no heavy image deps) so fayda_client stays import-light; mirrors
    the algorithm in core/image/image_generator.gregorian_to_ethiopian.
    """
    if not greg:
        return ""
    try:
        parts = greg.strip().replace("-", "/").split("/")
        if len(parts) != 3:
            return ""
        if len(parts[0]) == 4:              # YYYY/MM/DD (the Fayda API shape)
            gy, gm, gd = int(parts[0]), int(parts[1]), int(parts[2])
        else:                               # DD/MM/YYYY fallback
            gd, gm, gy = int(parts[0]), int(parts[1]), int(parts[2])

        from datetime import date as _date
        g = _date(gy, gm, gd)
        # Ethiopian new year (Meskerem 1) falls on Sep 11 (Sep 12 in a Gregorian
        # leap year), mapping to Ethiopian year = Gregorian year - 7 or - 8.
        leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
        new_year = _date(gy, 9, 12 if leap else 11)
        if g >= new_year:
            e_year = gy - 7
        else:
            prev_leap = ((gy - 1) % 4 == 0 and (gy - 1) % 100 != 0) or ((gy - 1) % 400 == 0)
            new_year = _date(gy - 1, 9, 12 if prev_leap else 11)
            e_year = gy - 8
        delta = (g - new_year).days          # 0-based day offset into the Eth year
        e_month = delta // 30 + 1            # months are 30 days; 13th is Pagume
        e_day = delta % 30 + 1
        return f"{e_day:02d}/{e_month:02d}/{e_year}"
    except Exception:
        return ""


# ── Session state (per user conversation) ─────────────────────────────────────
class FaydaSession:
    """Holds all in-flight state for one login attempt."""

    def __init__(self, fan: str):
        self.fan = fan
        self.verifier, self.challenge = _pkce_pair()
        self.state   = secrets.token_hex(16)
        self.nonce   = secrets.token_hex(16)

        # Filled in during the flow
        self.csrf_token:   Optional[str] = None
        self.csrf_header:  Optional[str] = None
        self.txn_id:       Optional[str] = None
        self.od_key:       Optional[str] = None
        self.od_hash:      Optional[str] = None
        self.auth_code:    Optional[str] = None
        self.jwt:          Optional[str] = None

        # httpx client — retries/backoff are handled by FaydaClient._send so a
        # single transient blip on Fayda's flaky servers doesn't kill the flow.
        self.client = httpx.AsyncClient(
            headers=COMMON_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0),
        )

    async def close(self):
        await self.client.aclose()


# ── The client ────────────────────────────────────────────────────────────────
class FaydaClient:
    """
    Usage:
        client = FaydaClient()
        session = await client.initiate(fan)   # steps 1-3, returns session
        await client.send_otp(session)          # step 4 — OTP sent to phone
        # … user types OTP …
        await client.verify_otp(session, otp)  # steps 5-6
        profile = await client.fetch_profile(session)  # steps 7-8
        await session.close()
    """

    # ── Internal: resilient request with retry + backoff ───────────────────────
    _RETRYABLE_EXC = (
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    )
    _RETRYABLE_STATUS = {502, 503, 504}

    async def _send(
        self,
        sess: "FaydaSession",
        method: str,
        url: str,
        *,
        retries: int = 2,
        label: str = "",
        **kwargs,
    ) -> httpx.Response:
        """
        Perform an HTTP request on the session client, retrying transient
        connection/timeout failures (and 502/503/504 gateway errors) with
        exponential backoff.

        Returns the final httpx.Response (caller handles raise_for_status / json).
        Raises FaydaConnectionError if the server stays unreachable.
        """
        name = label or url.rsplit("/", 1)[-1]
        last_exc: Optional[Exception] = None

        for attempt in range(1, retries + 2):  # retries=2 -> 3 attempts total
            try:
                r = await sess.client.request(method, url, **kwargs)
            except self._RETRYABLE_EXC as e:
                last_exc = e
                print(f"[FaydaClient] {name} attempt {attempt}/{retries + 1} "
                      f"failed: {type(e).__name__}: {e}")
                if attempt <= retries:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                break

            # Retry transient gateway errors too (Fayda's edge is flaky).
            if r.status_code in self._RETRYABLE_STATUS and attempt <= retries:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {r.status_code}", request=r.request, response=r
                )
                print(f"[FaydaClient] {name} attempt {attempt}/{retries + 1} "
                      f"got HTTP {r.status_code}; retrying…")
                await asyncio.sleep(1.5 * attempt)
                continue

            return r

        raise FaydaConnectionError(
            f"Could not reach the Fayda server (step: {name}) after "
            f"{retries + 1} attempts. The service may be temporarily down — "
            f"please try again shortly."
        ) from last_exc

    # ── Step 1-3: initiate ─────────────────────────────────────────────────────
    async def initiate(self, fan: str) -> FaydaSession:
        sess = FaydaSession(fan)
        await self._step1_authorize(sess)
        await self._step2_csrf(sess)
        await self._step3_oauth_details(sess)
        return sess

    async def _step1_authorize(self, sess: FaydaSession):
        """Seed session cookies."""
        params = {
            "client_id":             CLIENT_ID,
            "redirect_uri":          REDIRECT_URI,
            "response_type":         "code",
            "scope":                 SCOPE,
            "acr_values":            ACR,
            "code_challenge":        sess.challenge,
            "code_challenge_method": "S256",
            "nonce":                 sess.nonce,
            "state":                 sess.state,
            "display":               "page",
            "claims":                json.dumps({"userinfo": {"individual_id": {"essential": True}}}),
            "claims_locales":        "en am",
            "ui_locales":            "en",
        }
        r = await self._send(sess, "GET", f"{AUTH_BASE}/authorize",
                             params=params, label="authorize")
        # Cookies are stored automatically in sess.client.cookies
        r.raise_for_status()

    async def _step2_csrf(self, sess: FaydaSession):
        """Fetch the CSRF token."""
        r = await self._send(sess, "GET", f"{AUTH_BASE}/v1/esignet/csrf/token",
                             headers={"Accept": "application/json"}, label="csrf")
        r.raise_for_status()
        data = r.json()
        sess.csrf_token  = data["token"]
        sess.csrf_header = data.get("headerName", "X-XSRF-TOKEN")

    async def _step3_oauth_details(self, sess: FaydaSession):
        """Get transactionId and compute oauth-details-key/hash."""
        body = {
            "requestTime": _now_iso(),
            "request": {
                "nonce":               sess.nonce,
                "state":               sess.state,
                "clientId":            CLIENT_ID,
                "redirectUri":         REDIRECT_URI,
                "responseType":        "code",
                "scope":               SCOPE,
                "acrValues":           ACR,
                "claims":              {"userinfo": {"individual_id": {"essential": True}}},
                "claimsLocales":       "en am",
                "display":             "page",
                "maxAge":              None,
                "prompt":              None,
                "uiLocales":           "en",
                "codeChallenge":       sess.challenge,
                "codeChallengeMethod": "S256",
            },
        }
        r = await self._send(
            sess, "POST", f"{AUTH_BASE}/v1/esignet/authorization/v2/oauth-details",
            json=body, headers={sess.csrf_header: sess.csrf_token},
            label="oauth-details",
        )
        r.raise_for_status()
        data = r.json()
        resp_obj = data["response"]
        sess.txn_id  = resp_obj["transactionId"]
        sess.od_key  = sess.txn_id                  # same as transactionId
        sess.od_hash = _od_hash(resp_obj)

    # ── Step 4: send OTP ───────────────────────────────────────────────────────
    async def send_otp(self, sess: FaydaSession) -> dict:
        """Trigger OTP SMS/email to the user. Returns raw API response."""
        body = {
            "requestTime": _now_iso(),
            "request": {
                "transactionId": sess.txn_id,
                "individualId":  sess.fan,
                "otpChannels":   ["phone"],  # phone only; add "email" if needed
                "captchaToken":  None,
            },
        }
        r = await self._send(
            sess, "POST", f"{AUTH_BASE}/v1/esignet/authorization/send-otp",
            json=body,
            headers={
                sess.csrf_header:      sess.csrf_token,
                "oauth-details-key":   sess.od_key,
                "oauth-details-hash":  sess.od_hash,
            },
            label="send-otp",
        )
        r.raise_for_status()
        return r.json()

    # ── Step 5+6: verify OTP → get auth-code ──────────────────────────────────
    async def verify_otp(self, sess: FaydaSession, otp: str) -> dict:
        """Authenticate with OTP then immediately exchange for auth-code."""
        # Step 5: authenticate
        auth_body = {
            "requestTime": _now_iso(),
            "request": {
                "transactionId": sess.txn_id,
                "individualId":  sess.fan,
                "challengeList": [{
                    "authFactorType": "OTP",
                    "challenge":      otp,
                    "format":         "alpha-numeric",
                }],
            },
        }
        r = await self._send(
            sess, "POST", f"{AUTH_BASE}/v1/esignet/authorization/v2/authenticate",
            json=auth_body,
            headers={
                sess.csrf_header:      sess.csrf_token,
                "oauth-details-key":   sess.od_key,
                "oauth-details-hash":  sess.od_hash,
            },
            label="authenticate",
        )
        r.raise_for_status()
        auth_data = r.json()
        errors = auth_data.get("errors") or []
        if errors:
            raise ValueError(f"Authentication failed: {errors}")

        # Step 6: auth-code
        code_body = {
            "requestTime": _now_iso(),
            "request": {
                "transactionId":           sess.txn_id,
                "acceptedClaims":          [],
                "permittedAuthorizeScopes": [],
            },
        }
        r2 = await self._send(
            sess, "POST", f"{AUTH_BASE}/v1/esignet/authorization/auth-code",
            json=code_body,
            headers={
                sess.csrf_header:      sess.csrf_token,
                "oauth-details-key":   sess.od_key,
                "oauth-details-hash":  sess.od_hash,
            },
            label="auth-code",
        )
        r2.raise_for_status()
        code_data = r2.json()
        sess.auth_code = code_data["response"]["code"]
        return code_data

    # ── Steps 7+8: JWT → resident profile ─────────────────────────────────────
    async def fetch_profile(self, sess: FaydaSession, retries: int = 2) -> dict:
        """Exchange auth-code for JWT, then fetch resident profile.

        Retries on transient timeouts are handled by ``_send``; a persistent
        failure surfaces as FaydaConnectionError.
        """
        # Step 7: exchange code for JWT
        jwt_body = {
            "code":         sess.auth_code,
            "code_verifier": sess.verifier,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "nonce":         sess.nonce,
            "state":         sess.state,
        }
        r = await self._send(
            sess, "POST", f"{RESIDENT_BASE}/esignet/exchangeAutheCode",
            json=jwt_body, retries=retries, label="exchangeAutheCode",
        )
        r.raise_for_status()
        sess.jwt = r.text.strip().strip('"')

        # Step 8: fetch resident profile
        r2 = await self._send(
            sess, "POST", f"{RESIDENT_BASE}/esignet/exchangeResident",
            json={"headers": {"Content-Type": "application/json"}},
            headers={"X-Authorization": f"Bearer {sess.jwt}"},
            retries=retries, label="exchangeResident",
        )
        r2.raise_for_status()
        return r2.json()

    # ── Convenience: decode photo bytes from profile ───────────────────────────
    @staticmethod
    def extract_photo_bytes(profile: dict) -> Optional[bytes]:
        """Return raw photo bytes (JPEG) or None if not present."""
        b64 = profile.get("photo")
        if not b64:
            return None
        # Add padding if needed
        b64 += "=" * (-len(b64) % 4)
        return base64.b64decode(b64)

    # ── Convenience: map profile → text_data for image generators ─────────────
    @staticmethod
    def profile_to_text_data(profile: dict, fan: str = "", fin: str = "") -> dict:
        """
        Map the Fayda resident profile response into the text_data dict expected
        by generate_final_id_image / generate_final_id_image_b.

        Real response shape (from /esignet/exchangeResident):
            {
              "photo": "<base64>",
              "data": {
                "identity": {
                  "fullName": [{"language":"eng","value":"..."},{"language":"amh",...}],
                  "gender":   [{"language":"eng","value":"Male"}, ...],
                  "region"/"zone"/"woreda": [{language, value}, ...],
                  "dateOfBirth": "YYYY/MM/DD",
                  "phone": "09........",
                  "UIN": 390712813067            # 12-digit FIN
                }
              }
            }

        FIN is taken from the profile's UIN (authoritative 12-digit number); the
        user-typed ``fin`` is only a fallback. FAN (16-digit) comes from ``fan``.
        """
        identity = (profile.get("data") or {}).get("identity") or {}

        def _lang(field: str, lang: str) -> str:
            """Value for a language ('eng'/'amh') from a MOSIP language-array field."""
            arr = identity.get(field)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and item.get("language") == lang:
                        return str(item.get("value", "")).strip()
                for item in arr:                      # fall back to first value present
                    if isinstance(item, dict) and item.get("value") not in (None, ""):
                        return str(item["value"]).strip()
            elif arr not in (None, "", []):
                return str(arr).strip()
            return ""

        def _plain(*keys) -> str:
            for k in keys:
                v = identity.get(k)
                if v not in (None, "", []):
                    return str(v).strip()
            return ""

        name_en = _lang("fullName", "eng")
        name_am = _lang("fullName", "amh")
        if not name_am and name_en:
            try:
                import fidel
                name_am = fidel.Transliterate(name_en)
            except Exception:
                name_am = name_en

        dob_greg = _plain("dateOfBirth", "dob")      # 'YYYY/MM/DD'
        dob_et   = _greg_to_eth(dob_greg)            # 'DD/MM/YYYY' (Ethiopian)

        # FIN = 12-digit UIN from the profile (authoritative); FAN = 16-digit input.
        uin     = _plain("UIN", "uin", "idNumber")
        fin_val = (uin or fin or "").replace(" ", "").strip()
        fan_val = (fan or "").replace(" ", "").strip()

        return {
            "name_en":            name_en,
            "name_am":            name_am,
            "date_of_birth_greg": dob_greg,
            "date_of_birth_et":   dob_et,
            "sex_en":             _lang("gender", "eng"),
            "sex_am":             _lang("gender", "amh"),
            "phone_number":       _plain("phone", "phoneNumber"),
            "region_en":          _lang("region", "eng"),
            "region_am":          _lang("region", "amh"),
            "zone_en":            _lang("zone", "eng"),
            "zone_am":            _lang("zone", "amh"),
            "woreda_en":          _lang("woreda", "eng"),
            "woreda_am":          _lang("woreda", "amh"),
            "fan_code":           fan_val,                 # raw 16-digit (matches barcode)
            "fin_code":           _format_id(fin_val),     # '3907 1281 3067'
        }

