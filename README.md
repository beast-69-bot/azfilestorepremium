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

## New VPS Redeployment Guide (for `ag` User)

Follow these steps to redeploy the bot on a new VPS using a dedicated non-root user `ag`.

### 1. Connect and Create User
SSH into your VPS as root and create the `ag` user:
```bash
ssh root@YOUR_VPS_IP
```

Inside the root session, run:
```bash
adduser ag
usermod -aG sudo ag
su - ag
```

### 2. Install Dependencies
Update package lists and install required packages:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### 3. Clone Repository
Clone the repository into the user's home directory:
```bash
cd ~
git clone https://github.com/beast-69-bot/azfilestorepremium.git azfilestorepremium
cd azfilestorepremium
```

### 4. Setup Python Virtual Environment
Initialize a clean virtual environment and install project dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Configure Environment Variables
Create the `.env` configuration file:
```bash
cp .env.example .env
nano .env
```

Fill in the `.env` parameters (only the main details are required, payment settings can be set up via bot commands):
```env
BOT_TOKEN=NEW_BOT_TOKEN
OWNER_ID=YOUR_TELEGRAM_USER_ID
LINK_SECRET=LONG_RANDOM_SECRET
DB_PATH=data/bot.db
DB_BACKEND=sqlite
```

### 6. Restore from Backup (Optional)
If you have an old backup, restore it before starting the service:
```bash
mkdir -p ~/bot_backups
# Upload the backup archive to ~/bot_backups/ first, then run:
chmod +x scripts/restore_bot.sh
./scripts/restore_bot.sh ~/bot_backups/azfilestorepremium_backup_YYYYmmdd_HHMMSS.tar.gz
```
> [!IMPORTANT]
> If your old VPS has expired and you do not have a backup, the SQLite database, users, and files cannot be recovered. Having a backup is necessary to restore your data.

### 7. Create Systemd Service
Create a systemd service file to manage the bot lifecycle:
```bash
sudo tee /etc/systemd/system/azfilestorepremium.service > /dev/null <<EOF
[Unit]
Description=AZ File Store Premium Telegram Bot
After=network-online.target

[Service]
Type=simple
User=ag
WorkingDirectory=/home/ag/azfilestorepremium
ExecStart=/home/ag/azfilestorepremium/.venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

### 8. Start and Enable the Bot
Reload systemd daemon, enable autostart on boot, and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable azfilestorepremium
sudo systemctl restart azfilestorepremium
sudo systemctl status azfilestorepremium --no-pager
```

### 9. View Logs
To monitor live logs and debug issues, run:
```bash
journalctl -u azfilestorepremium -f
```

