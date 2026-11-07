"""Waychit payment-request client and webhook signature verification."""

import hashlib
import hmac
import json
import time
import urllib.request


def create_payment_request(
    *,
    api_key,
    amount,
    description,
    client_reference,
    success_redirect_url,
    failure_redirect_url,
    metadata=None,
    api_base="https://api.waychit.com",
    timeout=15,
):
    """Create a hosted Waychit payment request using server-side credentials."""
    payload = {
        "amount": float(amount),
        "description": description,
        "clientReference": client_reference,
        "successRedirectUrl": success_redirect_url,
        "failureRedirectUrl": failure_redirect_url,
    }
    if metadata:
        payload["metadata"] = metadata
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/v1/payment-requests",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_payment_request(response):
    """Normalize the documented paymentRequest response envelope."""
    if not isinstance(response, dict):
        return {}
    value = response.get("paymentRequest") or response.get("data") or response
    return value if isinstance(value, dict) else {}


def verify_waychit_signature(signature, raw_body, secret, *, now=None, tolerance_seconds=300):
    """Verify Waychit's timestamped HMAC-SHA256 signature and reject replays."""
    if not signature or not secret:
        return False
    try:
        parts = [part.strip() for part in signature.split(",")]
        timestamp_values = [part[2:] for part in parts if part.startswith("t=")]
        signatures = [part[3:] for part in parts if part.startswith("v1=")]
        if len(timestamp_values) != 1 or not signatures:
            return False
        timestamp = int(timestamp_values[0])
        current_time = int(time.time() if now is None else now)
        if abs(current_time - timestamp) > tolerance_seconds:
            return False
        body = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode("utf-8")
        signed_payload = str(timestamp).encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)
    except (TypeError, ValueError, UnicodeError):
        return False
