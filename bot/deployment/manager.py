import asyncio
import logging
from typing import Any, Dict, List, Optional
from telegram.ext import Application

logger = logging.getLogger(__name__)

# Global tracking for running instances and status
RUNNING_SUB_BOTS: Dict[str, Application] = {}
BOT_STATUS: Dict[str, str] = {}  # token -> status string
BOT_DEPLOY_LOGS: Dict[str, List[str]] = {}  # token -> list of checkpoints

# Task queue for sequential deployments
DEPLOY_QUEUE = asyncio.Queue()
_queue_worker_task: Optional[asyncio.Task] = None

def get_status(token: str) -> str:
    if token in RUNNING_SUB_BOTS:
        return "🟢 Running"
    return BOT_STATUS.get(token, "🔴 Offline")

def set_status(token: str, status: str):
    BOT_STATUS[token] = status

def log_checkpoint(token: str, checkpoint: str):
    if token not in BOT_DEPLOY_LOGS:
        BOT_DEPLOY_LOGS[token] = []
    BOT_DEPLOY_LOGS[token].append(checkpoint)
    # Limit to last 5 logs
    if len(BOT_DEPLOY_LOGS[token]) > 5:
        BOT_DEPLOY_LOGS[token].pop(0)

def get_deploy_logs(token: str) -> List[str]:
    return BOT_DEPLOY_LOGS.get(token, ["🔴 No logs available"])

async def start_queue_worker(db, cfg, defaults):
    global _queue_worker_task
    if _queue_worker_task is None:
        _queue_worker_task = asyncio.create_task(_worker(db, cfg, defaults))

async def _worker(db, cfg, defaults):
    from bot.handlers import start_sub_bot
    while True:
        token = await DEPLOY_QUEUE.get()
        try:
            set_status(token, "🟡 Deploying")
            log_checkpoint(token, "🔑 Decrypting Token")
            log_checkpoint(token, "📢 Verifying Log Channel")
            
            # Decrypt token before start
            from bot.security import decrypt_token
            decrypted = await decrypt_token(token, db)
            await start_sub_bot(decrypted, db, cfg, defaults)
            
            set_status(token, "🟢 Running")
            log_checkpoint(token, "🟢 Start Successful")
        except Exception as e:
            logger.error("Queue deployment failed for token: %s", e)
            set_status(token, "❌ Failed")
            log_checkpoint(token, f"❌ Failed: {str(e)[:30]}")
        finally:
            DEPLOY_QUEUE.task_done()

async def enqueue_deployment(token: str):
    set_status(token, "🟡 Deploying")
    log_checkpoint(token, "⏳ Added to Queue")
    await DEPLOY_QUEUE.put(token)
