import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet

from config import BOT_TOKEN

_DATA_DIR = os.getenv("USERDATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
_WALLETS_FILE = os.path.join(_DATA_DIR, "wallets.json")


def _fernet(user_id: int) -> Fernet:
    raw = f"{BOT_TOKEN}:{user_id}".encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _load() -> dict:
    if os.path.exists(_WALLETS_FILE):
        try:
            with open(_WALLETS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_WALLETS_FILE), exist_ok=True)
    with open(_WALLETS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_wallet(user_id: int, private_key: str, label: str = "") -> str:
    """Encrypt and save private key. Returns checksummed address."""
    from eth_account import Account
    from web3 import Web3

    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    account = Account.from_key(pk)
    address = account.address

    encrypted = _fernet(user_id).encrypt(pk.encode()).decode()
    data = _load()
    uid = str(user_id)
    wallets = data.setdefault(uid, [])

    for w in wallets:
        if w["address"].lower() == address.lower():
            w["encrypted_key"] = encrypted
            if label:
                w["label"] = label
            _save(data)
            return address

    wallets.append({
        "address": address,
        "encrypted_key": encrypted,
        "label": label or f"{address[:6]}…{address[-4:]}",
    })
    _save(data)
    return address


def get_wallets(user_id: int) -> list[dict]:
    """Return [{address, label}] for user (no private keys)."""
    return [
        {"address": w["address"], "label": w["label"]}
        for w in _load().get(str(user_id), [])
    ]


def get_account(user_id: int, address: str):
    """Return eth_account.Account for wallet. Raises if not found."""
    from eth_account import Account

    for w in _load().get(str(user_id), []):
        if w["address"].lower() == address.lower():
            pk = _fernet(user_id).decrypt(w["encrypted_key"].encode()).decode()
            return Account.from_key(pk)
    raise ValueError(f"Wallet \1 not found")


def remove_wallet(user_id: int, address: str) -> bool:
    data = _load()
    uid = str(user_id)
    before = len(data.get(uid, []))
    data[uid] = [w for w in data.get(uid, []) if w["address"].lower() != address.lower()]
    if len(data.get(uid, [])) < before:
        _save(data)
        return True
    return False
