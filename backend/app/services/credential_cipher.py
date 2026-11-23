"""「记住密码」的凭据加密（零外部依赖）。

为什么不直接用 `cryptography`/Fernet：
本项目的依赖清单刻意保持精简（生产镜像用 pip 装包），而凭据加密只处理
一条几十字节的口令。这里用标准库实现 **Encrypt-then-MAC 的 HMAC-CTR 流加密**：

    master = base64(CREDENTIAL_KEY)           # 32 字节，独立于 AUTH_SECRET
    salt   = random(16)                       # 每条记录独立
    enc_key, mac_key = HKDF-SHA256(master, salt, info="temubox-store-credential")

    keystream[i] = HMAC-SHA256(enc_key, nonce || counter_i)
    ciphertext   = plaintext XOR keystream
    tag          = HMAC-SHA256(mac_key, version || salt || nonce || ciphertext)

这不是自创算法：它等价于 AES-CTR + HMAC 的标准组合，安全性建立在
HMAC-SHA256 的伪随机性上；没有分组模式、padding oracle 或 IV 复用问题
（每条记录随机 salt + nonce，且名文极短）。

存储格式：`v1.<b64 salt>.<b64 nonce>.<b64 ciphertext>.<b64 tag>`

密钥留空时 `enabled()` 返回 False，调用方应关闭「记住密码」功能。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional

from app.config import CREDENTIAL_KEY

VERSION = "v1"
SALT_BYTES = 16
NONCE_BYTES = 16
TAG_BYTES = 32
INFO = b"temubox-store-credential"


class CredentialError(Exception):
    """密钥缺失/格式非法/校验失败。"""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _master_key(key: Optional[str] = None) -> bytes:
    material = (key if key is not None else CREDENTIAL_KEY or "").strip()
    if not material:
        raise CredentialError("未配置 CREDENTIAL_KEY，无法使用「记住密码」")
    try:
        raw = base64.b64decode(material, validate=True)
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都视为配置错误
        raise CredentialError("CREDENTIAL_KEY 不是合法的 base64") from exc
    if len(raw) != 32:
        raise CredentialError("CREDENTIAL_KEY 必须是 base64 编码的 32 字节密钥")
    return raw


def enabled(key: Optional[str] = None) -> bool:
    try:
        _master_key(key)
        return True
    except CredentialError:
        return False


def _hkdf(master: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, master, hashlib.sha256).digest()
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _keystream(enc_key: bytes, nonce: bytes, size: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < size:
        output += hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(output[:size])


def encrypt(plaintext: str, key: Optional[str] = None) -> str:
    """加密一条凭据，返回可入库的字符串（含版本、salt、nonce、tag）。"""
    master = _master_key(key)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    enc_key, mac_key = (
        _hkdf(master, salt, INFO, 64)[:32],
        _hkdf(master, salt, INFO, 64)[32:],
    )
    raw = plaintext.encode("utf-8")
    stream = _keystream(enc_key, nonce, len(raw))
    ciphertext = bytes(a ^ b for a, b in zip(raw, stream))
    tag = hmac.new(
        mac_key,
        VERSION.encode() + salt + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    return ".".join([VERSION, _b64encode(salt), _b64encode(nonce), _b64encode(ciphertext), _b64encode(tag)])


def decrypt(token: str, key: Optional[str] = None) -> str:
    """解密；任何篡改/密钥不匹配都抛 CredentialError（不返回部分明文）。"""
    master = _master_key(key)
    try:
        version, salt_text, nonce_text, ciphertext_text, tag_text = str(token).split(".")
    except ValueError as exc:
        raise CredentialError("凭据格式非法") from exc
    if version != VERSION:
        raise CredentialError("凭据版本不受支持")

    salt = _b64decode(salt_text)
    nonce = _b64decode(nonce_text)
    ciphertext = _b64decode(ciphertext_text)
    tag = _b64decode(tag_text)

    derived = _hkdf(master, salt, INFO, 64)
    enc_key, mac_key = derived[:32], derived[32:]
    expected = hmac.new(mac_key, version.encode() + salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise CredentialError("凭据校验失败（密钥不匹配或数据被篡改）")

    stream = _keystream(enc_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8")
