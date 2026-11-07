import hashlib
import hmac
import json
import time
from decimal import Decimal

from store.extensions import db
from store.models import Customer, Order, Payment


WEBHOOK_SECRET = "wave_test_webhook_secret"


def _create_pending_wave_order(app):
    with app.app_context():
        customer = Customer(
            full_name="Test Customer",
            phone_number="1234567",
            address="Test address",
        )
        order = Order(
            order_number="KORA-TEST-WAVE-001",
            customer=customer,
            status="pending",
            payment_status="pending",
            currency="GMD",
            total=Decimal("125.00"),
        )
        payment = Payment(
            order=order,
            provider="Wave",
            provider_reference="cos-test-wave-001",
            payer_phone_number="1234567",
            status="pending",
            amount=Decimal("125.00"),
        )
        db.session.add(order)
        db.session.add(payment)
        db.session.commit()
        return order.id


def _signed_headers(body, timestamp=None):
    timestamp = str(timestamp or int(time.time()))
    signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        timestamp.encode("ascii") + body,
        hashlib.sha256,
    ).hexdigest()
    return {"Wave-Signature": f"t={timestamp},v1={signature}"}


def _event_body(**overrides):
    checkout = {
        "id": "cos-test-wave-001",
        "client_reference": "KORA-TEST-WAVE-001",
        "amount": "125.00",
        "currency": "GMD",
        "payment_status": "succeeded",
        "checkout_status": "complete",
        "transaction_id": "wave-txn-test-001",
    }
    checkout.update(overrides)
    return json.dumps(
        {
            "id": "EV-test-wave-001",
            "type": "checkout.session.completed",
            "data": checkout,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_wave_webhook_confirms_matching_payment_and_is_idempotent(app, client):
    app.config["WAVE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    order_id = _create_pending_wave_order(app)
    body = _event_body()
    headers = _signed_headers(body)

    first = client.post("/payment/wave/webhook", data=body, headers=headers)
    second = client.post("/payment/wave/webhook", data=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.payment_status == "paid"
        assert order.status == "confirmed"
        assert order.receipt is not None
        assert order.payments[0].status == "paid"
        assert order.payments[0].provider_reference == "cos-test-wave-001"


def test_wave_webhook_rejects_invalid_signature(app, client):
    app.config["WAVE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    _create_pending_wave_order(app)
    response = client.post(
        "/payment/wave/webhook",
        data=_event_body(),
        headers={"Wave-Signature": "t=123,v1=invalid"},
    )
    assert response.status_code == 401


def test_wave_webhook_rejects_amount_mismatch(app, client):
    app.config["WAVE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    order_id = _create_pending_wave_order(app)
    body = _event_body(amount="1.00")
    response = client.post(
        "/payment/wave/webhook",
        data=body,
        headers=_signed_headers(body),
    )
    assert response.status_code == 409
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.payment_status == "pending"
        assert order.receipt is None


def test_wave_webhook_rejects_old_signature_timestamp(app, client):
    app.config["WAVE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    _create_pending_wave_order(app)
    body = _event_body()
    headers = _signed_headers(body, timestamp=int(time.time()) - 600)
    response = client.post("/payment/wave/webhook", data=body, headers=headers)
    assert response.status_code == 401


def test_wave_webhook_requires_signing_secret(client):
    response = client.post(
        "/payment/wave/webhook",
        data=b'{"type":"checkout.session.completed","data":{}}',
    )
    assert response.status_code == 503
