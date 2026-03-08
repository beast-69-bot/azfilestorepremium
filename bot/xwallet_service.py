"""
XWallet Payment Gateway service.

Base URL  : https://xwalletbot.shop/wallet/getway
Endpoints :
  GET /pay.php       → create payment, returns qr_code_id etc.
  GET /api_qr.php    → fetch QR image URL for a qr_code_id
  GET /check.php     → check payment status

Live-tested response shapes are handled exactly as received.
"""

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://xwalletbot.shop/wallet/getway"


async def create_payment(amount: float, api_key: str) -> dict:
    """
    Create a new XWallet payment request.

    GET /pay.php?key=<api_key>&amount=<amount>

    Returns the full response dict on success (status == "pending").
    Raises ValueError if the API returns an unexpected status.
    Raises on network/HTTP errors (let caller handle).
    """
    url = f"{BASE_URL}/pay.php"
    params = {"key": api_key, "amount": f"{amount:.2f}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                logger.info("create_payment response: %s", data)
                if data.get("status") != "pending":
                    raise ValueError(f"Unexpected status from XWallet: {data.get('status')} | full: {data}")
                return data
    except aiohttp.ClientError as e:
        logger.warning("Network error in create_payment: %s", e)
        raise
    except ValueError:
        raise
    except Exception as e:
        logger.warning("Unexpected error in create_payment: %s", e)
        raise


async def get_qr_data(qr_code_id: str) -> dict:
    """
    Fetch QR image URL and UPI details for a given qr_code_id.

    GET /api_qr.php?code=<qr_code_id>

    Returns dict with at minimum: qr_url, upi_url, expires_at.
    Raises ValueError if status != "success".
    """
    url = f"{BASE_URL}/api_qr.php"
    params = {"code": qr_code_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                logger.info("get_qr_data response: %s", data)
                if data.get("status") != "success":
                    raise ValueError(f"Unexpected status from api_qr: {data.get('status')} | full: {data}")
                return data
    except aiohttp.ClientError as e:
        logger.warning("Network error in get_qr_data: %s", e)
        raise
    except ValueError:
        raise
    except Exception as e:
        logger.warning("Unexpected error in get_qr_data: %s", e)
        raise


async def check_payment_status(qr_code_id: str) -> str:
    """
    Check current payment status for a qr_code_id.

    GET /check.php?code=<qr_code_id>

    Returns status string exactly as received:
      "TXN_SUCCESS" → paid
      "pending"     → still waiting
      "FAILED"      → payment failed

    Returns "pending" on network/transient errors so the polling loop
    continues until actual timeout rather than dying early.
    """
    url = f"{BASE_URL}/check.php"
    params = {"code": qr_code_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                status = data.get("status", "pending")
                return str(status)
    except Exception as e:
        logger.warning("Error in check_payment_status for %s: %s", qr_code_id, e)
        return "pending"


# Alias for backward compatibility
check_status = check_payment_status


async def wait_for_payment(
    qr_code_id: str,
    timeout_minutes: int = 5,
    poll_interval: int = 5,
) -> bool:
    """
    Poll check_payment_status() every `poll_interval` seconds until:
      - status == "TXN_SUCCESS" → return True
      - status == "FAILED"      → return False
      - timeout reached          → return False

    Network errors return "pending" so polling continues safely.
    Uses asyncio.sleep between polls to avoid blocking the event loop.
    """
    max_attempts = (timeout_minutes * 60) // poll_interval

    for attempt in range(max_attempts):
        status = await check_payment_status(qr_code_id)
        logger.info("Polling %s (attempt %d/%d): %s", qr_code_id, attempt + 1, max_attempts, status)

        if status == "TXN_SUCCESS":
            return True
        if status == "FAILED":
            return False

        await asyncio.sleep(poll_interval)

    logger.info("Payment polling timed out for %s after %d minutes", qr_code_id, timeout_minutes)
    return False


# Keep get_qr_image_url available (not called in current flow, but retained per project rules)
get_qr_image_url = get_qr_data
