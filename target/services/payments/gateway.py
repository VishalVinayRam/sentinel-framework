"""Payment gateway client for payments-service.

BUG (tracked: SENT-1102): requests.post has no timeout and no retry
policy. A single gateway blip blocks the worker thread indefinitely,
exhausting the thread pool and causing 100% failure rate.
"""
import os
import requests

GATEWAY_URL = os.environ.get("GATEWAY_URL", "https://api.stripe.com/v1")
_API_KEY = os.environ.get("STRIPE_SECRET_KEY", "")


def charge(amount_cents: int, card_token: str, idempotency_key: str) -> dict:
    # BUG: no timeout — blocks the calling thread indefinitely on gateway hang
    resp = requests.post(              # ← hangs forever if gateway is slow
        f"{GATEWAY_URL}/charges",
        data={
            "amount":   amount_cents,
            "currency": "usd",
            "source":   card_token,
        },
        headers={
            "Authorization":  f"Bearer {_API_KEY}",
            "Idempotency-Key": idempotency_key,
        },
        # timeout=5 is missing — must add
    )
    resp.raise_for_status()            # ← propagates 5xx directly, no retry
    return resp.json()


def refund(charge_id: str, amount_cents: int) -> dict:
    # BUG: same missing timeout pattern
    resp = requests.post(
        f"{GATEWAY_URL}/refunds",
        data={"charge": charge_id, "amount": amount_cents},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    resp.raise_for_status()
    return resp.json()
