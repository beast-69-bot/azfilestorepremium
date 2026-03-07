with open(r'bot\config.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace(
    '    mongo_db_name: str = "azfilestorepremium"',
    '    mongo_db_name: str = "azfilestorepremium"\n    xwallet_api_key: str = ""\n    payment_gateway: str = "manual"'
)

text = text.replace(
    '        mongo_db_name = os.getenv("MONGO_DB_NAME", "azfilestorepremium").strip()',
    '        mongo_db_name = os.getenv("MONGO_DB_NAME", "azfilestorepremium").strip()\n        xwallet_api_key = os.getenv("XWALLET_API_KEY", "").strip()\n        payment_gateway = os.getenv("PAYMENT_GATEWAY", "manual").strip()'
)

text = text.replace(
    '            mongo_db_name=mongo_db_name,\n        )',
    '            mongo_db_name=mongo_db_name,\n            xwallet_api_key=xwallet_api_key,\n            payment_gateway=payment_gateway,\n        )'
)

# Might fail if CRLF
text = text.replace(
    '            mongo_db_name=mongo_db_name,\r\n        )',
    '            mongo_db_name=mongo_db_name,\r\n            xwallet_api_key=xwallet_api_key,\r\n            payment_gateway=payment_gateway,\r\n        )'
)

with open(r'bot\config.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open(r'.env.example', 'a', encoding='utf-8') as f:
    f.write('\n# XWallet Payment Gateway\nXWALLET_API_KEY=your_xwallet_api_key_here\nPAYMENT_GATEWAY=manual\n')

with open(r'requirements.txt', 'a', encoding='utf-8') as f:
    f.write('\naiohttp>=3.9.0\n')
