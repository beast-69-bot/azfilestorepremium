# Admin-Controlled File Distribution Bot (Normal + Premium)

Python Telegram bot that:
- Stores files on Telegram (via `file_id`)
- Generates two deep-links per file (Normal + Premium)
- Enforces force-join channels on every access
- Enforces premium gating in real time
- Supports one-time tokens to grant 1-day premium
- Supports batch links (Normal + Premium)
- Adds a global default caption for link-delivered files

## Setup (Windows / PowerShell)

1. Create `.env` from `.env.example`
2. Install deps:
```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```
3. Run:
```powershell
python main.py
```

## Database Backends
Supported:
- `sqlite` (default)
- `mongo` (MongoDB Atlas)

### SQLite (default)
```env
DB_BACKEND=sqlite
DB_PATH=data/bot.db
```

### MongoDB Atlas
```env
DB_BACKEND=mongo
MONGO_URI=mongodb+srv://<user>:<pass>@<cluster-url>/?retryWrites=true&w=majority
MONGO_DB_NAME=azfilestorepremium
```

## SQLite -> Mongo Atlas Migration
Run once after creating your Atlas database/user/network access:
```bash
python scripts/migrate_sqlite_to_mongo.py \
  --sqlite data/bot.db \
  --mongo-uri "mongodb+srv://<user>:<pass>@<cluster-url>/?retryWrites=true&w=majority" \
  --mongo-db azfilestorepremium
```

Then switch `.env`:
```env
DB_BACKEND=mongo
MONGO_URI=...
MONGO_DB_NAME=azfilestorepremium
```

## Force Channels
- Add the bot to each required channel.
- For private channels, provide an invite link when adding via `/forcech add`.

## VPS Migration (Backup/Restore)
Scripts:
- `scripts/backup_bot.sh`
- `scripts/restore_bot.sh`

Backup on old VPS:
```bash
cd ~/azfilestorepremium
chmod +x scripts/backup_bot.sh scripts/restore_bot.sh
./scripts/backup_bot.sh
```

This creates a timestamped archive in `~/bot_backups/` containing:
- `data/bot.db`
- `.env`

Restore on new VPS:
```bash
cd ~/azfilestorepremium
chmod +x scripts/backup_bot.sh scripts/restore_bot.sh
./scripts/restore_bot.sh /path/to/azfilestorepremium_backup_YYYYmmdd_HHMMSS.tar.gz
```

Optional env vars:
- `PROJECT_DIR` (default: `~/azfilestorepremium`)
- `SERVICE_NAME` (default: `azfilestorepremium`)
- `BACKUP_DIR` (backup script only, default: `~/bot_backups`)
