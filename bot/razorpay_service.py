"""
Razorpay Dynamic UPI QR Code Service.

API documentation: https://razorpay.com/docs/payments/payments/qr-codes/
Endpoints:
  POST https://api.razorpay.com/v1/payments/qr_codes  -> Create single-use QR Code
  GET  https://api.razorpay.com/v1/payments            -> List payments, filter by qr_code_id to verify capture
"""

import logging
import asyncio
import aiohttp
from aiohttp import BasicAuth

logger = logging.getLogger(__name__)

BASE_URL = "https://api.razorpay.com/v1"


async def create_qr_code(
    amount_rs: int,
    order_id: int,
    key_id: str,
    key_secret: str,
    user_id: int,
    username: str,
    plan_label: str,
) -> dict:
    """
    Generate a dynamic single-use UPI QR code for a specific user and plan.
    Amount is converted from Rupees to Paise (multiplied by 100).
    """
    url = f"{BASE_URL}/payments/qr_codes"

    payload = {
        "type": "upi_qr",
        "name": f"Premium Store User {user_id}",
        "usage": "single_use",
        "fixed_amount": True,
        "payment_amount": amount_rs * 100,  # Razorpay requires amount in paise.
        "description": f"Order #{order_id} for Plan: {plan_label}"[:250],
        "notes": {
            "order_id": str(order_id),
            "user_id": str(user_id),
            "username": username,
            "plan_label": plan_label,
        }
    }

    auth = BasicAuth(key_id, key_secret)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                data = await response.json()
                if response.status >= 400:
                    logger.error(
                        "Razorpay create_qr_code failed: status=%s response=%s",
                        response.status,
                        data,
                    )
                    response.raise_for_status()
                logger.info("Razorpay create_qr_code success: %s", data.get("id"))
                return data
    except aiohttp.ClientError as e:
        logger.error("Razorpay HTTP request failed: %s", e)
        raise
    except Exception as e:
        logger.error("Unexpected error in create_qr_code: %s", e)
        raise


async def check_payment_status(
    qr_code_id: str,
    key_id: str,
    key_secret: str,
) -> str:
    """
    Check if a payment has been successfully captured for the given qr_code_id.
    Filters payment transactions by qr_code_id.
    Returns:
      "TXN_SUCCESS" -> if a payment is captured/authorized
      "pending"     -> if no captured payment is found yet
    """
    url = f"{BASE_URL}/payments/qr_codes/{qr_code_id}/payments"

    auth = BasicAuth(key_id, key_secret)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response.raise_for_status()
                data = await response.json()
                items = data.get("items", [])

                for payment in items:
                    status = payment.get("status")
                    if status in ("captured", "authorized"):
                        logger.info("Razorpay payment detected successfully for QR %s: status=%s", qr_code_id, status)
                        return "TXN_SUCCESS"

                return "pending"
    except Exception as e:
        logger.warning("Error checking payment status for Razorpay QR %s: %s", qr_code_id, e)
        return "pending"


async def wait_for_payment(
    qr_code_id: str,
    key_id: str,
    key_secret: str,
    timeout_minutes: int = 5,
    poll_interval: int = 6,
) -> bool:
    """
    Poll Razorpay status every `poll_interval` seconds until transaction succeeds or times out.
    """
    max_attempts = (timeout_minutes * 60) // poll_interval

    for attempt in range(max_attempts):
        status = await check_payment_status(qr_code_id, key_id, key_secret)
        logger.info("Polling Razorpay QR %s (attempt %d/%d): %s", qr_code_id, attempt + 1, max_attempts, status)

        if status == "TXN_SUCCESS":
            return True

        await asyncio.sleep(poll_interval)

    logger.info("Razorpay payment polling timed out for QR %s after %d minutes", qr_code_id, timeout_minutes)
    return False
