"""「记住密码」凭据加密的单元测试（零依赖 HMAC-CTR + Encrypt-then-MAC）。"""
import base64
import os

import pytest

from app.services import credential_cipher

KEY = base64.b64encode(os.urandom(32)).decode()
OTHER_KEY = base64.b64encode(os.urandom(32)).decode()


def test_roundtrip_preserves_unicode_and_special_chars():
    for plaintext in ["p@ssw0rd", "中文密码-2026", "", "a" * 200]:
        token = credential_cipher.encrypt(plaintext, KEY)
        assert credential_cipher.decrypt(token, KEY) == plaintext


def test_ciphertext_is_not_plaintext_and_changes_every_time():
    token_a = credential_cipher.encrypt("same-secret", KEY)
    token_b = credential_cipher.encrypt("same-secret", KEY)
    assert "same-secret" not in token_a
    assert token_a != token_b, "每条记录必须使用独立 salt/nonce"


def test_tampered_ciphertext_is_rejected():
    token = credential_cipher.encrypt("secret", KEY)
    version, salt, nonce, ciphertext, tag = token.split(".")
    flipped = ciphertext[:-1] + ("A" if ciphertext[-1] != "A" else "B")
    with pytest.raises(credential_cipher.CredentialError):
        credential_cipher.decrypt(".".join([version, salt, nonce, flipped, tag]), KEY)


def test_wrong_key_is_rejected():
    token = credential_cipher.encrypt("secret", KEY)
    with pytest.raises(credential_cipher.CredentialError):
        credential_cipher.decrypt(token, OTHER_KEY)


def test_invalid_key_and_token_shapes_are_rejected():
    assert credential_cipher.enabled(KEY) is True
    assert credential_cipher.enabled("") is False
    assert credential_cipher.enabled(base64.b64encode(b"short").decode()) is False
    with pytest.raises(credential_cipher.CredentialError):
        credential_cipher.encrypt("x", "not-base64!!")
    with pytest.raises(credential_cipher.CredentialError):
        credential_cipher.decrypt("garbage", KEY)
    with pytest.raises(credential_cipher.CredentialError):
        credential_cipher.decrypt("v2.a.b.c.d", KEY)
