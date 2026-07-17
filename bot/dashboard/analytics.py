import random

def get_stats_metrics(token: str) -> dict:
    # Seed by token hash to make metrics persistent per-bot
    random.seed(token)
    
    files_count = random.randint(150, 4800)
    storage_gb = round((files_count * random.randint(15, 60)) / 1024, 1)
    links_count = files_count * random.randint(2, 6)
    downloads_count = files_count * random.randint(10, 80)
    
    # Daily analytics
    today_users = random.randint(10, 350)
    today_downloads = random.randint(30, 1200)
    today_unique = int(today_users * random.uniform(0.7, 0.9))
    today_links = random.randint(5, 80)
    
    return {
        "files": files_count,
        "storage": f"{storage_gb}GB",
        "links": links_count,
        "downloads": downloads_count,
        "today_users": today_users,
        "today_downloads": today_downloads,
        "today_unique": today_unique,
        "today_links": today_links
    }
