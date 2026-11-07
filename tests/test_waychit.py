import hashlib
import hmac

from store.waychit import extract_payment_request, verify_waychit_signature


def _signature(secret, timestamp, body):
    payload = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_waychit_signature_accepts_valid_signature():
    body = b'{"type":"payment.request.completed"}'
    assert verify_waychit_signature(_signature("secret", 1000, body), body, "secret", now=1000)


def test_waychit_signature_rejects_expired_signature():
    body = b'{}'
    assert not verify_waychit_signature(_signature("secret", 1000, body), body, "secret", now=1400)


def test_waychit_signature_rejects_tampered_body():
    body = b'{"amount":10}'
    signature = _signature("secret", 1000, body)
    assert not verify_waychit_signature(signature, b'{"amount":11}', "secret", now=1000)


def test_waychit_signature_rejects_missing_secret_or_header():
    assert not verify_waychit_signature("", b"{}", "secret", now=1000)
    assert not verify_waychit_signature("t=1000,v1=abc", b"{}", "", now=1000)


def test_extract_payment_request_handles_documented_envelope():
    assert extract_payment_request({"paymentRequest": {"id": "pr_1"}}) == {"id": "pr_1"}
