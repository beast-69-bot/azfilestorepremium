import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

file_path = r"c:\Users\anshu\OneDrive\Documents\filepremiumstore\azfilestorepremium\bot\handlers.py"
with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def _pay_plan_keyboard" in line:
        print(f"Line {i+1}: {line.strip()}")
