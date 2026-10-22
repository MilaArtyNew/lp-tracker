"""Local encrypted wallet store. Migration must finish before serving requests.

Only migrate_wallets may read legacy ciphertext. Runtime operations accept v2
only. No exception crossing this module's public boundary includes input data.
"""
import base64
from functools import wraps
import hashlib
import hmac
import json
import os
import re
import tempfile

from cryptography.fernet import Fernet
from eth_account import Account

from config import WALLET_MASTER_KEY, WALLET_LEGACY_BOT_TOKEN

_DATA_DIR = os.getenv("USERDATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
_WALLETS_FILE = os.path.join(_DATA_DIR, "wallets.json")


class WalletStoreError(ValueError):
    """Sanitized fail-closed error safe for ordinary application logging."""


def _sanitized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            raise WalletStoreError(
                "Wallet operation failed; check wallet storage and encryption configuration."
            ) from None
    return wrapped


def _master_bytes() -> bytes:
    encoded = WALLET_MASTER_KEY.encode("ascii")
    raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
    if len(raw) != 32 or base64.urlsafe_b64encode(raw) != encoded:
        raise ValueError("Invalid master key")
    return raw


def _uid(user_id) -> str:
    uid = str(user_id)
    if not re.fullmatch(r"[1-9][0-9]*", uid):
        raise ValueError("Invalid wallet owner")
    return uid


def _fernet(user_id: int) -> Fernet:
    # Domain-separated HMAC KDF: random 256-bit master, distinct key per owner.
    key = hmac.new(_master_bytes(), f"lp-wallet-v2:{_uid(user_id)}".encode(), hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Non-standard JSON constant")


def _schema(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("Invalid wallet store")
    for uid, rows in data.items():
        if not isinstance(uid, str):
            raise ValueError("Invalid wallet owner")
        _uid(uid)
        if not isinstance(rows, list):
            raise ValueError("Invalid wallet rows")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid wallet row")
            for field in ("address", "label", "encrypted_key"):
                if not isinstance(row.get(field), str):
                    raise ValueError("Invalid wallet field")
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", row["address"]) or not row["encrypted_key"]:
                raise ValueError("Invalid wallet field")
            version = row.get("encryption_version", 1)
            if type(version) is not int or version not in (1, 2):
                raise ValueError("Unsupported wallet encryption version")


def _read():
    try:
        with open(_WALLETS_FILE, "rb") as f:
            original = f.read()
    except FileNotFoundError:
        return None, {}
    data = json.loads(original, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _schema(data)
    return original, data


def _decrypt(uid, row, *, legacy=False) -> bytes:
    if row.get("encryption_version", 1) == 1:
        if not legacy or not WALLET_LEGACY_BOT_TOKEN:
            raise ValueError("Wallet migration requires legacy secret")
        raw = f"{WALLET_LEGACY_BOT_TOKEN}:{uid}".encode()
        cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
    else:
        cipher = _fernet(uid)
    private_key = cipher.decrypt(row["encrypted_key"].encode()).decode("ascii")
    account = Account.from_key(private_key)
    if account.address.lower() != row["address"].lower():
        raise ValueError("Wallet address verification failed")
    return private_key.encode("ascii")


def _verify(data):
    _master_bytes()
    _schema(data)
    for uid, rows in data.items():
        for row in rows:
            _decrypt(uid, row)


def _fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file(payload: bytes, *, backup=False):
    directory = os.path.dirname(os.path.abspath(_WALLETS_FILE))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    prefix = "wallets.json.pre-v2-" if backup else ".wallets-"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".bak" if backup else ".tmp")
    keep = False
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if backup:
            _fsync_directory(directory)
            keep = True
        else:
            os.replace(temporary, _WALLETS_FILE)
            _fsync_directory(directory)
    finally:
        if not keep and os.path.exists(temporary):
            os.unlink(temporary)


@_sanitized
def migrate_wallets() -> int:
    """Verify entire store, back up encrypted v1 bytes, migrate atomically.

    Returns number of legacy rows converted. Repeated v2-only calls verify but
    do not rewrite or create backups. Call with all other writers stopped.
    """
    _master_bytes()  # Required even for fresh/empty installations.
    original, data = _read()
    count = sum(row.get("encryption_version", 1) == 1 for rows in data.values() for row in rows)
    if not count:
        _verify(data)
        if original is not None:
            os.chmod(_WALLETS_FILE, 0o600)
        return 0
    if not WALLET_LEGACY_BOT_TOKEN:
        raise ValueError("Wallet migration requires legacy secret")
    _write_file(original, backup=True)
    ownership_counts = {uid: len(rows) for uid, rows in data.items()}
    # First validate/decrypt ALL rows (including existing v2) before conversion.
    plaintext = {(uid, i): _decrypt(uid, row, legacy=True)
                 for uid, rows in data.items() for i, row in enumerate(rows)}
    for uid, rows in data.items():
        for i, row in enumerate(rows):
            if row.get("encryption_version", 1) == 1:
                row["encrypted_key"] = _fernet(uid).encrypt(plaintext[uid, i]).decode()
                row["encryption_version"] = 2
            if _decrypt(uid, row) != plaintext[uid, i]:
                raise ValueError("Wallet encryption roundtrip failed")
    if ownership_counts != {uid: len(rows) for uid, rows in data.items()}:
        raise ValueError("Wallet ownership counts changed")
    _save(data)
    return count


@_sanitized
def _load() -> dict:
    _original, data = _read()
    _verify(data)
    return data


@_sanitized
def _save(data: dict) -> None:
    _verify(data)
    _write_file(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8"))


@_sanitized
def add_wallet(user_id: int, private_key: str, label: str = "") -> str:
    """Encrypt and save private key. Returns checksummed address."""
    uid = _uid(user_id)
    pk = private_key.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    address = Account.from_key(pk).address
    encrypted = _fernet(uid).encrypt(pk.encode()).decode()
    data = _load()
    wallets = data.setdefault(uid, [])
    for row in wallets:
        if row["address"].lower() == address.lower():
            row["encrypted_key"] = encrypted
            row["encryption_version"] = 2
            if label:
                row["label"] = label
            _save(data)
            return address
    wallets.append({"address": address, "encrypted_key": encrypted,
                    "label": label or f"{address[:6]}…{address[-4:]}", "encryption_version": 2})
    _save(data)
    return address


@_sanitized
def get_wallets(user_id: int) -> list[dict]:
    """Return [{address, label}] for user (no private keys)."""
    return [{"address": row["address"], "label": row["label"]}
            for row in _load().get(_uid(user_id), [])]


@_sanitized
def get_account(user_id: int, address: str):
    """Return eth_account.Account for wallet. Raises if not found."""
    uid = _uid(user_id)
    for row in _load().get(uid, []):
        if row["address"].lower() == address.lower():
            return Account.from_key(_decrypt(uid, row).decode("ascii"))
    raise ValueError("Wallet not found")


@_sanitized
def remove_wallet(user_id: int, address: str) -> bool:
    data = _load()
    uid = _uid(user_id)
    before = len(data.get(uid, []))
    data[uid] = [row for row in data.get(uid, []) if row["address"].lower() != address.lower()]
    if len(data[uid]) < before:
        _save(data)
        return True
    return False
