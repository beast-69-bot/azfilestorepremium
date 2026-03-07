import logging
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

async def create_payment(amount: float, order_id: str, api_key: str) -> dict:
    url = "https://xwalletbot.shop/pay.php"
    params = {"key": api_key, "amount": str(amount)}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                if data.get("status") != "pending":
                    raise ValueError(f"Expected pending status, got: {data.get('status')}")
                return data
    except Exception as e:
        logger.warning(f"Error in create_payment: {e}")
        raise

async def get_qr_image_url(qr_code_id: str) -> dict:
    url = "https://xwalletbot.shop/api_qr.php"
    params = {"code": qr_code_id}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                return data
    except Exception as e:
        logger.warning(f"Error in get_qr_image_url: {e}")
        raise

async def check_payment_status(qr_code_id: str) -> str:
    url = "https://xwalletbot.shop/check.php"
    params = {"code": qr_code_id}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                return data.get("status", "FAILED")
    except Exception as e:
        logger.warning(f"Error in check_payment_status: {e}")
        return "FAILED"

async def wait_for_payment(qr_code_id: str, timeout_minutes: int = 10, poll_interval: int = 5) -> bool:
    max_attempts = (timeout_minutes * 60) // poll_interval
    
    for _ in range(max_attempts):
        try:
            status = await check_payment_status(qr_code_id)
            if status == "TXN_SUCCESS":
                return True
            if status == "FAILED":
                return False
        except Exception as e:
            logger.warning(f"Error checking payment: {e}")
        
        await asyncio.sleep(poll_interval)
    
    return False
