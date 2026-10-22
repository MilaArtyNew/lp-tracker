"""Offline encryption migration tests: disposable accounts, no API or live data."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import traceback

from cryptography.fernet import Fernet
from eth_account import Account
import pytest

from modules import wallet_manager as wm


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "wallets.json"
    monkeypatch.setattr(wm, "_WALLETS_FILE", str(path))
    monkeypatch.setattr(wm, "WALLET_MASTER_KEY", Fernet.generate_key().decode(), raising=False)
    monkeypatch.setattr(wm, "WALLET_LEGACY_BOT_TOKEN", "disposable-old-token", raising=False)
    return path


def legacy(uid, *, version=None):
    account = Account.create()
    key = base64.urlsafe_b64encode(hashlib.sha256(f"disposable-old-token:{uid}".encode()).digest())
    row = {"address": account.address, "label": "Test label λ", "extra": {"keep": [1, 2]},
           "encrypted_key": Fernet(key).encrypt(account.key.hex().encode()).decode()}
    if version is not None:
        row["encryption_version"] = version
    return row, account


def put(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path.read_bytes()


def migrate():
    fn = getattr(wm, "migrate_wallets", None)
    assert callable(fn), "startup migration is not implemented"
    return fn()


def backups(path):
    return list(path.parent.glob("wallets.json.pre-v2-*.bak"))


def test_migrate_legacy_preserves_ownership_fields_counts_and_backup(store):
    row1, account1 = legacy(111)
    row2, account2 = legacy(222, version=1)
    original = put(store, {"111": [row1], "222": [row2], "333": []})
    assert migrate() == 2
    result = json.loads(store.read_bytes())
    assert set(result) == {"111", "222", "333"}
    assert [len(rows) for rows in result.values()] == [1, 1, 0]
    for uid, old, account in [(111, row1, account1), (222, row2, account2)]:
        new = result[str(uid)][0]
        assert new["encryption_version"] == 2
        assert new["encrypted_key"] != old["encrypted_key"]
        assert {k: v for k, v in new.items() if k not in ("encryption_version", "encrypted_key")} == {
            k: v for k, v in old.items() if k not in ("encryption_version", "encrypted_key")}
        assert wm.get_account(uid, account.address).address == account.address
    assert len(backups(store)) == 1
    assert backups(store)[0].read_bytes() == original
    assert stat.S_IMODE(backups(store)[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_migration_is_idempotent_and_independent_of_both_bot_tokens(store, monkeypatch):
    row, account = legacy(111)
    put(store, {"111": [row]})
    migrate()
    before, mtime = store.read_bytes(), store.stat().st_mtime_ns
    monkeypatch.setattr(wm, "WALLET_LEGACY_BOT_TOKEN", "")
    monkeypatch.setenv("BOT_TOKEN", "different-new-telegram-token")
    assert migrate() == 0
    assert store.read_bytes() == before and store.stat().st_mtime_ns == mtime
    assert len(backups(store)) == 1
    assert wm.get_account(111, account.address).address == account.address


@pytest.mark.parametrize("existing", [False, True])
def test_empty_store_requires_no_legacy_token(store, monkeypatch, existing):
    monkeypatch.setattr(wm, "WALLET_LEGACY_BOT_TOKEN", "")
    if existing:
        put(store, {})
    assert migrate() == 0
    assert wm.get_wallets(111) == []
    assert not backups(store)


@pytest.mark.parametrize("master", ["", "invalid-secret-marker", "A" * 44, base64.urlsafe_b64encode(b"x" * 31).decode()])
def test_invalid_or_missing_master_fails_even_for_empty_store(store, monkeypatch, master):
    monkeypatch.setattr(wm, "WALLET_MASTER_KEY", master)
    with pytest.raises(ValueError, match="Wallet"):
        migrate()
    assert not store.exists()


@pytest.mark.parametrize("secret", ["", "wrong-legacy-secret"])
def test_missing_or_wrong_legacy_secret_never_rewrites(store, monkeypatch, secret):
    row, _ = legacy(111)
    before = put(store, {"111": [row]})
    monkeypatch.setattr(wm, "WALLET_LEGACY_BOT_TOKEN", secret)
    with pytest.raises(ValueError, match="Wallet"):
        migrate()
    assert store.read_bytes() == before


@pytest.mark.parametrize("corruption", ["cipher", "key", "address", "version", "label", "missing", "rows", "owner", "bool-version"])
def test_bad_row_aborts_entire_migration_without_partial_write(store, corruption):
    good, _ = legacy(111)
    bad, _ = legacy(222)
    data = {"111": [good], "222": [bad]}
    if corruption == "cipher":
        bad["encrypted_key"] = "broken-ciphertext"
    elif corruption == "key":
        key = base64.urlsafe_b64encode(hashlib.sha256(b"disposable-old-token:222").digest())
        bad["encrypted_key"] = Fernet(key).encrypt(b"invalid-private-key-marker").decode()
    elif corruption == "address":
        bad["address"] = Account.create().address
    elif corruption == "version":
        bad["encryption_version"] = 3
    elif corruption == "bool-version":
        bad["encryption_version"] = True
    elif corruption == "label":
        bad["label"] = 5
    elif corruption == "missing":
        del bad["encrypted_key"]
    elif corruption == "rows":
        data["222"] = {}
    elif corruption == "owner":
        data["not-a-user"] = data.pop("222")
    before = put(store, data)
    with pytest.raises(ValueError, match="Wallet"):
        migrate()
    assert store.read_bytes() == before


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null", b'{"111": [], "111": []}', b'{"111": [], "x": NaN}'])
def test_corrupt_json_never_loads_as_empty_or_rewrites(store, raw):
    store.write_bytes(raw)
    with pytest.raises(ValueError, match="Wallet"):
        wm._load()
    with pytest.raises(ValueError, match="Wallet"):
        migrate()
    assert store.read_bytes() == raw


def test_new_wallet_is_explicit_v2_with_user_isolation(store):
    account = Account.create()
    wm.add_wallet(111, account.key.hex(), "New")
    row = json.loads(store.read_bytes())["111"][0]
    assert row["encryption_version"] == 2
    put(store, {"222": [row]})
    with pytest.raises(ValueError, match="Wallet"):
        wm.get_account(222, account.address)


def test_mixed_store_keeps_v2_ciphertext_unchanged(store):
    account = Account.create()
    wm.add_wallet(111, account.key.hex(), "New")
    data = json.loads(store.read_bytes())
    v2 = data["111"][0].copy()
    row, old = legacy(222)
    data["222"] = [row]
    put(store, data)
    assert migrate() == 1
    assert json.loads(store.read_bytes())["111"][0] == v2
    assert wm.get_account(222, old.address).address == old.address


def test_wrong_master_aborts_mixed_store_and_runtime_load(store, monkeypatch):
    account = Account.create()
    wm.add_wallet(111, account.key.hex())
    data = json.loads(store.read_bytes())
    row, _ = legacy(222)
    data["222"] = [row]
    before = put(store, data)
    monkeypatch.setattr(wm, "WALLET_MASTER_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError, match="Wallet"):
        migrate()
    with pytest.raises(ValueError, match="Wallet"):
        wm.get_wallets(111)
    assert store.read_bytes() == before


def test_runtime_rejects_unmigrated_store(store):
    row, _ = legacy(111)
    put(store, {"111": [row]})
    with pytest.raises(ValueError, match="Wallet"):
        wm._load()


def test_exception_tracebacks_are_sanitized(store, monkeypatch):
    row, _ = legacy(111)
    row["encrypted_key"] = "ciphertext-marker"
    put(store, {"111": [row]})
    try:
        migrate()
    except ValueError:
        text = traceback.format_exc()
    else:
        pytest.fail("corruption accepted")
    assert "ciphertext-marker" not in text
    assert "disposable-old-token" not in text
    assert wm.WALLET_MASTER_KEY not in text
    secret_marker = "invalid-private-key-marker"
    with pytest.raises(ValueError) as exc:
        wm.add_wallet(111, secret_marker)
    assert secret_marker not in str(exc.value)


def test_atomic_failure_retains_original_backup_and_cleans_tempfiles(store, monkeypatch):
    row, _ = legacy(111)
    before = put(store, {"111": [row]})
    def fail_replace(*args):
        raise OSError("sensitive-storage-error-marker")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ValueError, match="Wallet") as exc:
        migrate()
    assert "sensitive-storage-error-marker" not in str(exc.value)
    assert store.read_bytes() == before
    assert len(backups(store)) == 1 and backups(store)[0].read_bytes() == before
    assert not list(store.parent.glob(".wallets-*"))


def test_migration_fsyncs_backup_and_atomic_write_before_replace(store, monkeypatch):
    row, _ = legacy(111)
    put(store, {"111": [row]})
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    def fsync(fd):
        events.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)
    def replace(src, dst):
        assert stat.S_IMODE(os.stat(src).st_mode) == 0o600
        events.append("replace")
        real_replace(src, dst)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    migrate()
    assert events.count("file") >= 2
    assert events.index("replace") > events.index("file")
    assert events[-1] == "dir"


def test_runtime_save_and_update_preserve_version_and_fields(store):
    account = Account.create()
    wm.add_wallet(111, account.key.hex(), "First")
    data = wm._load()
    data["111"][0]["extra"] = {"kept": True}
    wm._save(data)
    wm.add_wallet(111, account.key.hex(), "Second")
    assert wm.get_wallets(111) == [{"address": account.address, "label": "Second"}]
    assert wm._load()["111"][0]["extra"] == {"kept": True}
    assert wm._load()["111"][0]["encryption_version"] == 2
    assert wm.remove_wallet(111, account.address)
    assert wm.get_wallets(111) == []
    assert not wm.remove_wallet(111, account.address)


def test_startup_migration_precedes_bot_construction_and_all_network_calls():
    # Parse source only: do not import or invoke the bot or any real API.
    tree = ast.parse((Path(__file__).parents[1] / "bot.py").read_text())
    main = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
    first = main.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert ast.unparse(first.value.func) == "wallet_manager.migrate_wallets"
