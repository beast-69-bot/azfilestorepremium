sa# Admin-Controlled File Distribution Bot (Normal + Premium)

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

## Force Channels
- Add the bot to each required channel.
- For private channels, provide an invite link when adding via `/forcech add`.

