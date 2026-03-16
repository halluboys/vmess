import json
import os

MAIN_FILE = "refresh-tokens.json"
NEW_FILE = "token_baru.json"

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data if isinstance(data, list) else []

main_data = load_json(MAIN_FILE)
new_data = load_json(NEW_FILE)

merged = {}

for item in main_data:
    number = str(item.get("number"))
    if number:
        merged[number] = item

for item in new_data:
    number = str(item.get("number"))
    if number:
        merged[number] = item

result = list(merged.values())

with open(MAIN_FILE, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

if os.path.exists(NEW_FILE):
    os.remove(NEW_FILE)

print(f"Merge selesai. Total token: {len(result)}")