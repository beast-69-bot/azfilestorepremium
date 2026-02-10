# Admin-Controlled File Distribution Bot (Normal + Premium)

Python Telegram bot that:
- Stores files on Telegram (via `file_id`)
- Generates two deep-links per file (Normal + Premium)
- Enforces force-join channels on every access
- Enforces premium gating in real time
- Supports one-time tokens to grant 1-day premium
- Supports batch links (Normal + Premium)
- Adds a global default caption for link-delivered files

No referral system.

## Roles
- Owner: full control, can add/remove admins, has all admin permissions
- Admin: upload files, generate links, manage premium users, generate tokens, set/remove captions, broadcast, view stats
- User: can access files only via generated links, must join required channels, can redeem tokens for premium

## Core Access Rules
- Each stored file has two different deep links (Normal and Premium).
- Normal link: requires force-join; accessible by normal + premium users
- Premium link: requires force-join + active premium; accessible only by premium users
- Channel joining is mandatory for both link types.
- Links can be forwarded, but access checks are enforced on every open (force-join + premium gating).

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

## Environment Variables
- `BOT_TOKEN`: BotFather token
- `OWNER_ID`: your Telegram numeric user id
- `LINK_SECRET`: reserved for future link signing/hardening; set to a long random string
- `DB_PATH`: optional, defaults to `data/bot.db`

## Command List
- `/start` and deep links: user opens `https://t.me/<bot_username>?start=<code>`
- `/getlink`
- `/batch`
- `/custombatch`
- `/addadmin`
- `/removeadmin`
- `/addpremium`
- `/removepremium`
- `/gencode`
- `/redeem`
- `/forcech`
- `/broadcast`
- `/stats`
- `/setcaption`
- `/removecaption`
- `/settime`

## Usage (Owner/Admin)

### Upload File (Fastest)
1. Send any `document`/`video`/`audio`/`photo` to the bot in private chat (as Owner/Admin).
2. Bot will save it and reply with two deep links (Normal + Premium).

### /getlink
- Reply to a file message: `/getlink`
- Or use a stored id: `/getlink <file_id>`

### /batch
Channel post range batch:
1. Send `/batch`
2. Bot will ask for the STARTING channel post link (example `https://t.me/<channel>/123` or `https://t.me/c/<id>/123`)
3. Send the ENDING channel post link
4. Bot verifies it is admin in that channel, then generates 2 links:
   - Normal batch link (force-join required)
   - Premium batch link (force-join + premium required)

Cancel: `/batch cancel`

### /custombatch
1. Send `/custombatch`
2. Bot will say: "Files / media bhejo..."
3. Send multiple files/media to the bot
4. After each file, bot shows a confirmation message with buttons:
   - "Generate Link" to finalize and generate Normal + Premium batch links
   - "Cancel Process" to cancel and clear the temporary list

### Admin Management (Owner Only)
- `/addadmin <user_id>`
- `/removeadmin <user_id>`

### Premium Management
- `/addpremium <user_id> [days]`
- `/removepremium <user_id>`

### Token System
- Admin generates: `/gencode`
- User redeems: `/redeem <token>`
Token rules:
- One-time use
- Redeemable by one user only
- Grants 1 day premium
- Becomes invalid after use

### Force Channels
- `/forcech add <channel_id> [invite_link]`
- `/forcech remove <channel_id>`
- `/forcech list`

Important:
- The bot must be able to verify membership via `getChatMember`.
- Add the bot to every required channel.
- For channels where the bot cannot verify (missing permissions, not a member/admin, etc.), the bot will deny access (fail-closed).
- For private channels, you should provide `invite_link` so users can join.

### Caption System
- Set default caption: `/setcaption <text>` (or reply to a text message with `/setcaption`)
- Remove caption: `/removecaption`
- Caption is automatically applied when sending files via any generated link (single or batch).

### Auto-Delete Delivered Messages
- `/settime <seconds|5m|1h|off>`
- When enabled, bot will auto-delete files/messages it delivers via links after the configured time.

### Broadcast
- Reply to any message, then send `/broadcast`

### Stats
- `/stats`

## Deployment Notes
- This bot runs in polling mode by default (`main.py`).
- For production VPS use, keep it running under a process manager (Windows Task Scheduler, NSSM, systemd on Linux).
- Webhook mode is not implemented in this template; polling is simpler and reliable for most deployments.

## VPS Setup Guide (Ubuntu + systemd, Polling)
These steps assume Ubuntu 22.04/24.04 (similar for Debian).

### 1) Create a server user (recommended)
```bash
adduser tg-bot
usermod -aG sudo tg-bot
```

### 2) Install dependencies
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 3) Clone and install
```bash
sudo -iu tg-bot
git clone https://github.com/beast-69-bot/azfilestorepremium.git
cd azfilestorepremium

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4) Configure environment
```bash
cp .env.example .env
nano .env
```
Set:
- `BOT_TOKEN`
- `OWNER_ID`
- `LINK_SECRET` (long random string)
- `DB_PATH` (keep default unless you want another location)

Optional hardening:
```bash
chmod 600 .env
```

### 5) Create a systemd service
Create `/etc/systemd/system/azfilestorepremium.service`:
```ini
[Unit]
Description=AzFileStorePremium Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tg-bot
WorkingDirectory=/home/tg-bot/azfilestorepremium
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/tg-bot/azfilestorepremium/.venv/bin/python /home/tg-bot/azfilestorepremium/main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable + start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now azfilestorepremium
```

Logs:
```bash
sudo journalctl -u azfilestorepremium -f
```

### 6) Updating the bot on VPS
```bash
sudo -iu tg-bot
cd azfilestorepremium
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart azfilestorepremium
```

### Common VPS Issues
- Force-join checks fail:
  - Add the bot to each required channel.
  - If Telegram denies `getChatMember`, the bot denies access (fail-closed).
- Token/links not working after rename:
  - Deep links use `https://t.me/<bot_username>?start=...`; bot username change requires users to use new links.

## Database
SQLite at `DB_PATH` (default `data/bot.db`) stores:
- Users and premium expiry
- Admin list
- Files (Telegram `file_id` based)
- Links (deep-link codes for normal/premium and file/batch targets)
- Tokens (one-time, premium grants)
- Force channels
- Caption setting

## Welcome Banner (1:1)
Square welcome banner SVG:
- `assets/welcome_banner.svg`

Export to PNG (1024x1024) examples:
```bash
# Linux (Inkscape)
inkscape assets/welcome_banner.svg --export-type=png --export-filename=assets/welcome_banner.png --export-width=1024 --export-height=1024
```
