#!/usr/bin/env bash
# fayda_flow.sh — eSignet OTP login → tokens → resident profile
# Own-account testing only. Reads OTP interactively.
set -euo pipefail

FAN="${1:?Usage: ./fayda_flow.sh <YOUR_FAN_or_FCN>}"
WORK="fayda_out"; mkdir -p "$WORK"

AUTH="https://auth.fayda.et"
RESIDENT="https://api-resident.fayda.et"
CLIENT_ID="ajcCvwQcVm1dAr6HaW4Y5ObnDmwPPTpH9oDaZSPPrpo"
REDIRECT_URI="https://resident.fayda.et/callback"
SCOPE="openid profile email"
ACR="mosip:idp:acr:generated-code"

now() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# ── PKCE + state + nonce ─────────────────────────────────────────
VERIFIER=$(openssl rand -base64 48 | tr -d '=+/\n' | cut -c1-43)
CHALLENGE=$(printf '%s' "$VERIFIER" | openssl dgst -sha256 -binary | b64url)
STATE=$(openssl rand -hex 16)
NONCE=$(openssl rand -hex 16)
echo "$VERIFIER" > "$WORK/code_verifier.txt"
echo "[+] PKCE ready — verifier saved to $WORK/code_verifier.txt"

JAR="$WORK/cookies.txt"   # cookie jar holds XSRF cookie + session

# ── 1. Hit /authorize to land on the login SPA & seed cookies ─────
echo "=== [1] GET /authorize (seed session/cookies) ==="
Q="client_id=$CLIENT_ID&redirect_uri=$(printf '%s' "$REDIRECT_URI" | jq -sRr @uri)"
Q="$Q&response_type=code&scope=$(printf '%s' "$SCOPE" | jq -sRr @uri)"
Q="$Q&acr_values=$(printf '%s' "$ACR" | jq -sRr @uri)"
Q="$Q&code_challenge=$CHALLENGE&code_challenge_method=S256"
Q="$Q&nonce=$NONCE&state=$STATE&display=page"
Q="$Q&claims=$(printf '%s' '{"userinfo":{"individual_id":{"essential":true}}}' | jq -sRr @uri)"
Q="$Q&claims_locales=$(printf '%s' 'en am' | jq -sRr @uri)&ui_locales=en"

curl -s -c "$JAR" -b "$JAR" -L "$AUTH/authorize?$Q" -o "$WORK/authorize.html" -w "  http=%{http_code}\n"

# ── 2. CSRF token ────────────────────────────────────────────────
echo "=== [2] GET /v1/esignet/csrf/token ==="
CSRF_JSON=$(curl -s -c "$JAR" -b "$JAR" "$AUTH/v1/esignet/csrf/token" -H "Accept: application/json")
echo "$CSRF_JSON" | jq .
CSRF=$(echo "$CSRF_JSON" | jq -r '.token')
HDR_NAME=$(echo "$CSRF_JSON" | jq -r '.headerName // "X-XSRF-TOKEN"')
echo "  $HDR_NAME = ${CSRF:0:16}…"

# ── 3. oauth-details → transactionId + oauth-details-key/hash ─────
echo "=== [3] POST /v1/esignet/authorization/v2/oauth-details ==="
DETAILS_BODY=$(jq -n --arg rt "$(now)" --arg n "$NONCE" --arg s "$STATE" \
  --arg cid "$CLIENT_ID" --arg ru "$REDIRECT_URI" --arg sc "$SCOPE" \
  --arg acr "$ACR" --arg cc "$CHALLENGE" '{
    requestTime:$rt,
    request:{
      nonce:$n, state:$s, clientId:$cid, redirectUri:$ru,
      responseType:"code", scope:$sc, acrValues:$acr,
      claims:{userinfo:{individual_id:{essential:true}}},
      claimsLocales:"en am", display:"page", maxAge:null,
      prompt:null, uiLocales:"en",
      codeChallenge:$cc, codeChallengeMethod:"S256"
    }}')

curl -s -D "$WORK/details.hdr" -c "$JAR" -b "$JAR" \
  -X POST "$AUTH/v1/esignet/authorization/v2/oauth-details" \
  -H "Content-Type: application/json" -H "$HDR_NAME: $CSRF" \
  -d "$DETAILS_BODY" -o "$WORK/details.json"
jq . "$WORK/details.json"

TXN=$(jq -r '.response.transactionId' "$WORK/details.json")
# oauth-details-key / oauth-details-hash are returned as response headers
OD_KEY="$TXN"
OD_HASH=$(jq -c '.response' "$WORK/details.json" \
          | tr -d '\n' \
          | openssl dgst -sha256 -binary \
          | openssl base64 -A \
          | tr '+/' '-_' \
          | tr -d '=')
echo "  transactionId = $TXN"
echo "  oauth-details-key  = ${OD_KEY:0:16}…"
echo "  oauth-details-hash = ${OD_HASH:0:16}…"

# ── 4. send-otp ──────────────────────────────────────────────────
echo "=== [4] POST /v1/esignet/authorization/send-otp ==="
curl -s -c "$JAR" -b "$JAR" -X POST "$AUTH/v1/esignet/authorization/send-otp" \
  -H "Content-Type: application/json" \
  -H "$HDR_NAME: $CSRF" \
  -H "oauth-details-key: $OD_KEY" \
  -H "oauth-details-hash: $OD_HASH" \
  -d "$(jq -n --arg rt "$(now)" --arg txn "$TXN" --arg fan "$FAN" '{
        requestTime:$rt,
        request:{ transactionId:$txn, individualId:$fan,
                  otpChannels:["email","phone"], captchaToken:null } }')" \
  | tee "$WORK/send_otp.json" | jq .

read -rp ">>> Enter OTP received on your phone/email: " OTP

# ── 5. authenticate (verify OTP) ─────────────────────────────────
echo "=== [5] POST /v1/esignet/authorization/v2/authenticate ==="
curl -s -c "$JAR" -b "$JAR" -X POST "$AUTH/v1/esignet/authorization/v2/authenticate" \
  -H "Content-Type: application/json" \
  -H "$HDR_NAME: $CSRF" \
  -H "oauth-details-key: $OD_KEY" \
  -H "oauth-details-hash: $OD_HASH" \
  -d "$(jq -n --arg rt "$(now)" --arg txn "$TXN" --arg fan "$FAN" --arg otp "$OTP" '{
        requestTime:$rt,
        request:{ transactionId:$txn, individualId:$fan,
          challengeList:[{ authFactorType:"OTP", challenge:$otp,
                           format:"alpha-numeric" }] } }')" \
  | tee "$WORK/authenticate.json" | jq .

# ── 6. auth-code ─────────────────────────────────────────────────
echo "=== [6] POST /v1/esignet/authorization/auth-code ==="
curl -s -c "$JAR" -b "$JAR" -X POST "$AUTH/v1/esignet/authorization/auth-code" \
  -H "Content-Type: application/json" \
  -H "$HDR_NAME: $CSRF" \
  -H "oauth-details-key: $OD_KEY" \
  -H "oauth-details-hash: $OD_HASH" \
  -d "$(jq -n --arg rt "$(now)" --arg txn "$TXN" '{
        requestTime:$rt,
        request:{ transactionId:$txn, acceptedClaims:[],
                  permittedAuthorizeScopes:[] } }')" \
  | tee "$WORK/auth_code.json" | jq .

CODE=$(jq -r '.response.code' "$WORK/auth_code.json")
echo "  auth code = $CODE"

# ── 8. exchangeAutheCode → JWT ───────────────────────────────────
echo "=== [8] POST /esignet/exchangeAutheCode ==="
JWT=$(curl -s -X POST "$RESIDENT/esignet/exchangeAutheCode" \
  -H "Content-Type: application/json" \
  -H "Origin: https://resident.fayda.et" \
  -H "Referer: https://resident.fayda.et/" \
  -d "$(jq -n --arg code "$CODE" --arg v "$VERIFIER" --arg ru "$REDIRECT_URI" \
        --arg cid "$CLIENT_ID" --arg n "$NONCE" --arg s "$STATE" '{
        code:$code, code_verifier:$v, redirect_uri:$ru,
        client_id:$cid, nonce:$n, state:$s }')")
echo "$JWT" > "$WORK/jwt.txt"
echo "  JWT = ${JWT:0:40}…"
# inspect claims
echo "$JWT" | cut -d. -f2 | tr '_-' '/+' | \
  awk '{l=length($0)%4; if(l==2)$0=$0"=="; else if(l==3)$0=$0"="; print}' | \
  openssl base64 -d -A 2>/dev/null | jq . || true

# ── 9. exchangeResident → profile (name + photo) ─────────────────
echo "=== [9] POST /esignet/exchangeResident ==="
curl -s -X POST "$RESIDENT/esignet/exchangeResident" \
  -H "Content-Type: application/json" \
  -H "X-Authorization: Bearer $JWT" \
  -H "Origin: https://resident.fayda.et" \
  -H "Referer: https://resident.fayda.et/" \
  -d '{"headers":{"Content-Type":"application/json"}}' \
  | tee "$WORK/resident.json" | jq '.photo="<BASE64_JPEG omitted>"'

# decode photo if present
jq -r '.photo // empty' "$WORK/resident.json" | base64 -d > "$WORK/photo.jpg" 2>/dev/null \
  && echo "  photo saved to $WORK/photo.jpg"

# ── 10. printableCredentialRoute (was CORS-failing in your capture) ─
echo "=== [10] POST /printableCredentialRoute ==="
UIN=$(jq -r '.uin // empty' "$WORK/resident.json")
SIG=$(jq -r '.signature // empty' "$WORK/resident.json")
curl -s -D "$WORK/printable.hdr" -X POST "$RESIDENT/printableCredentialRoute" \
  -H "Content-Type: application/json" \
  -H "X-Authorization: Bearer $JWT" \
  -H "Origin: https://resident.fayda.et" \
  -H "Referer: https://resident.fayda.et/" \
  -d "$(jq -n --arg uin "$UIN" --arg sig "$SIG" '{uin:$uin, signature:$sig}')" \
  -o "$WORK/printable.out" -w "  http=%{http_code}\n"
head -c 500 "$WORK/printable.out"; echo

echo "[+] Artifacts in $WORK/: jwt.txt, resident.json, photo.jpg, printable.out, cookies.txt"
