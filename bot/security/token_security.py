import os
from cryptography.fernet import Fernet

def generate_key() -> str:
    return Fernet.generate_key().decode()

async def get_encryption_key(db) -> bytes:
    # 1. Check environment variable first
    key = os.environ.get("CLONER_ENCRYPTION_KEY")
    if key:
        try:
            # Ensure valid Fernet key
            Fernet(key.encode())
            return key.encode()
        except Exception:
            pass
        
    # 2. Check database settings table
    key = await db.get_setting("cloner_encryption_key")
    if not key:
        key = generate_key()
        await db.set_setting("cloner_encryption_key", key)
        
    return key.encode()

async def encrypt_token(token: str, db) -> str:
    if not token:
        return ""
    key = await get_encryption_key(db)
    fernet = Fernet(key)
    return fernet.encrypt(token.encode()).decode()

async def decrypt_token(encrypted_token: str, db) -> str:
    if not encrypted_token:
        return ""
    # Fernet tokens always start with 'gAAAA'
    if not encrypted_token.startswith("gAAAA"):
        return encrypted_token
    try:
        key = await get_encryption_key(db)
        fernet = Fernet(key)
        return fernet.decrypt(encrypted_token.encode()).decode()
    except Exception:
        # If decryption fails, return as-is (e.g. key mismatch or unencrypted)
        return encrypted_token
