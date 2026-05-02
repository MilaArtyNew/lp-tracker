import json
import os

STORAGE_FILE = os.path.join(os.path.dirname(__file__), "users.json")


def _load() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {}
    with open(STORAGE_FILE) as f:
        return json.load(f)


def _save(data: dict):
    with open(STORAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def save_wallet(user_id: int, wallet: str):
    data = _load()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid]["wallet"] = wallet.lower()
    _save(data)


def get_wallet(user_id: int) -> str | None:
    data = _load()
    return data.get(str(user_id), {}).get("wallet")
