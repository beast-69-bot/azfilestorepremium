import asyncio
import json
import logging
import os
import sys
import time

# Add root folder to python path to import bot modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from bot.config import Config
from bot.db import Database
from bot.db_mongo import MongoDatabase
import bot.razorpay_service as rz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("RazorpayDiagnostic")

async def test_endpoint(session, url, auth, label):
    logger.info(f"  Querying Endpoint [{label}]: {url}...")
    try:
        async with session.get(url, auth=auth, timeout=10) as response:
            res_status = response.status
            res_data = await response.json()
            logger.info(f"    Response Code [{label}]: {res_status}")
            
            items = res_data.get("items", [])
            if not items:
                logger.info(f"    ℹ️ [{label}] No payments recorded yet (items list is empty).")
            else:
                logger.info(f"    🎉 [{label}] Found {len(items)} payment records:")
                for idx, payment in enumerate(items):
                    p_id = payment.get("id")
                    p_status = payment.get("status")
                    p_amount = payment.get("amount", 0) / 100 # convert to Rupees
                    logger.info(f"      Payment #{idx+1}: ID={p_id}, Status={p_status}, Amount={p_amount} INR")
                    if p_status in ("captured", "authorized"):
                        logger.info(f"      ✅ [{label}] Successful payment detected!")
            return res_status, res_data
    except Exception as ex:
        logger.error(f"    ❌ [{label}] Request failed: {ex}")
        return None, None

async def main():
    load_dotenv()
    
    # 1. Load configuration
    cfg = Config.from_env()
    logger.info("Configuration loaded:")
    logger.info(f"  DB Backend: {cfg.db_backend}")
    logger.info(f"  Payment Gateway: {cfg.payment_gateway}")
    logger.info(f"  Razorpay Key ID: {'SET (ends with ' + cfg.razorpay_key_id[-4:] + ')' if cfg.razorpay_key_id and len(cfg.razorpay_key_id) > 4 else 'NOT SET'}")
    logger.info(f"  Razorpay Key Secret: {'SET (ends with ' + cfg.razorpay_key_secret[-4:] + ')' if cfg.razorpay_key_secret and len(cfg.razorpay_key_secret) > 4 else 'NOT SET'}")
    
    if not cfg.razorpay_key_id or not cfg.razorpay_key_secret:
        logger.error("❌ Razorpay Key ID or Secret is not configured in .env!")
        return

    # 2. Connect to Database
    logger.info("Connecting to Database...")
    if cfg.db_backend == "mongo":
        if not cfg.mongo_uri:
            logger.error("❌ Mongo URI is empty in config!")
            return
        db = MongoDatabase(cfg.mongo_uri, cfg.mongo_db_name)
    else:
        db = Database(cfg.db_path)
        
    try:
        await db.init()
        logger.info("✅ Database connection successful!")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Database: {e}")
        return

    # 3. Test Razorpay Credentials (Create dummy QR)
    logger.info("Testing Razorpay credentials by generating a dummy QR Code...")
    try:
        # Create a test QR code for 1 INR
        qr_response = await rz.create_qr_code(
            amount_rs=1,
            order_id=999999,
            key_id=cfg.razorpay_key_id,
            key_secret=cfg.razorpay_key_secret,
            user_id=cfg.owner_id,
            username="DiagnosticTest",
            plan_label="Diagnostic 1 Day"
        )
        logger.info("✅ Dummy QR Code generated successfully!")
        logger.info(f"  QR Code ID: {qr_response.get('id')}")
        logger.info(f"  Short URL: {qr_response.get('short_url')}")
        logger.info(f"  Status: {qr_response.get('status')}")
    except Exception as e:
        logger.error(f"❌ Failed to generate test QR code: {e}")
        logger.error("Please double-check that your RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are correct and active.")
        await db.close()
        return

    # 4. Fetch Pending Orders and Poll Status
    logger.info("Fetching pending/submitted payments from the database...")
    try:
        if cfg.db_backend == "mongo":
            cursor = db.db.payment_requests.find({"status": {"$in": ["pending", "submitted"]}})
            pending_orders = await cursor.to_list(length=10)
        else:
            cur = await db.conn.execute(
                "SELECT * FROM payment_requests WHERE status IN ('pending', 'submitted') ORDER BY id DESC LIMIT 10"
            )
            cols = [col[0] for col in cur.description]
            rows = await cur.fetchall()
            await cur.close()
            pending_orders = [dict(zip(cols, r)) for r in rows]

        logger.info(f"Found {len(pending_orders)} pending payment requests in the database:")
        
        import aiohttp
        from aiohttp import BasicAuth
        auth = BasicAuth(cfg.razorpay_key_id, cfg.razorpay_key_secret)
        
        async with aiohttp.ClientSession() as session:
            for order in pending_orders:
                rid = order.get("id")
                user_id = order.get("user_id")
                amount = order.get("amount_rs")
                status = order.get("status")
                gateway_extra = order.get("gateway_extra")
                
                logger.info(f"--- Order #{rid} ---")
                logger.info(f"  User ID: {user_id}")
                logger.info(f"  Amount: {amount} INR")
                logger.info(f"  DB Status: {status}")
                
                if not gateway_extra:
                    logger.warning("  ⚠️ No 'gateway_extra' data found for this order! Cannot poll status.")
                    continue
                    
                try:
                    extra_data = json.loads(gateway_extra)
                    qr_code_id = extra_data.get("qr_code_id")
                except Exception as e:
                    logger.error(f"  ❌ Failed to parse gateway_extra: {e}")
                    continue
                    
                if not qr_code_id:
                    logger.warning("  ⚠️ No qr_code_id found in gateway_extra!")
                    continue
                    
                logger.info(f"  QR Code ID: {qr_code_id}")
                
                # Test Endpoint A: Dedicated payments list for QR code
                url_a = f"{rz.BASE_URL}/payments/qr_codes/{qr_code_id}/payments"
                await test_endpoint(session, url_a, auth, "Endpoint A - Dedicated QR")
                
                # Test Endpoint B: Filtered general payments list
                url_b = f"{rz.BASE_URL}/payments?qr_code_id={qr_code_id}"
                await test_endpoint(session, url_b, auth, "Endpoint B - Filtered Payments")
                
    except Exception as e:
        logger.error(f"❌ Error while fetching or checking database records: {e}")

    await db.close()
    logger.info("Diagnostic completed!")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
