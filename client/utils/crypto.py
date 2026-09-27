"""API Key 等敏感字段的 AES-GCM 加密存储（PRD §6.2；CLAUDE.md：敏感字段单独 AES 加密）。

密钥：首次运行时生成，存 %APPDATA%/KuJi/.secret（0600 权限）。
"""
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SECRET_FILE = "secret.key"


def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA", str(Path.home()))
    path = Path(base) / "KuJi"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_or_create_key() -> bytes:
    key_path = _appdata_dir() / _SECRET_FILE
    if key_path.exists():
        return key_path.read_bytes()
    key = AESGCM.generate_key(bit_length=128)
    key_path.write_bytes(key)
    try:  # 权限保护（Windows 上尽力而为）
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def encrypt_secret(plaintext: str) -> str:
    """加密：返回 hex（nonce + ciphertext）。"""
    key = _load_or_create_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return (nonce + ct).hex()


def decrypt_secret(token: str) -> str:
    """解密：encrypt_secret 的逆操作；失败返回空串（Key 更换/损坏时不阻断启动）。"""
    try:
        key = _load_or_create_key()
        raw = bytes.fromhex(token)
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
    except Exception:
        return ""
