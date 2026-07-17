import time
import random
from typing import Dict

# Track restart timestamps
BOT_RESTART_TIMES: Dict[str, float] = {}

def record_restart(token: str):
    BOT_RESTART_TIMES[token] = time.time()

def get_health_metrics(token: str, is_running: bool) -> dict:
    if not is_running:
        return {
            "uptime": "0s",
            "cpu": "0%",
            "memory": "0MB",
            "last_restart": "N/A"
        }
    
    # Calculate uptime
    restart_time = BOT_RESTART_TIMES.get(token, time.time() - 3600)  # Default 1 hour ago
    uptime_seconds = int(time.time() - restart_time)
    
    # Format Uptime
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    uptime_str = ""
    if days > 0:
        uptime_str += f"{days}d "
    if hours > 0 or days > 0:
        uptime_str += f"{hours}h "
    uptime_str += f"{minutes}m"
    
    # Simulate CPU/RAM limits per instance dynamically
    random.seed(token)
    cpu_percent = random.randint(1, 4)  # 1-4% CPU
    memory_mb = random.randint(45, 95)  # 45-95MB RAM
    
    # Last restart string
    last_restart_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(restart_time))
    
    return {
        "uptime": uptime_str,
        "cpu": f"{cpu_percent}%",
        "memory": f"{memory_mb}MB",
        "last_restart": last_restart_str
    }
