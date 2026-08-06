"""Feishu callback security checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from Crypto.Cipher import AES


class FeishuRequestVerifier:
    def __init__(self, *, verification_token: str, signing_secret: str | None = None) -> None:
        self._verification_token = verification_token
        # Feishu "Encrypt Key" is used for both payload decryption and request signatures.
        self._signing_secret = signing_secret

    def verify_token(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self._verification_token)

    def requires_signature(
        self,
        *,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
    ) -> bool:
        if not self._signing_secret:
            return False
        return bool(timestamp and nonce and signature)

    def verify_signature(
        self,
        *,
        body: bytes,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
    ) -> bool:
        if not self._signing_secret:
            return True
        if not timestamp or not nonce or not signature:
            return False
        expected = build_signature(
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            signing_secret=self._signing_secret,
        )
        return hmac.compare_digest(expected.lower(), signature.lower())

    def decode_body(self, body: bytes) -> bytes:
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("invalid Feishu callback payload")
        encrypted = raw.get("encrypt")
        if encrypted is None:
            return body
        if not self._signing_secret:
            raise ValueError("encrypted Feishu payload requires Encrypt Key")
        return decrypt_feishu_encrypt(self._signing_secret, encrypted).encode("utf-8")


def build_signature(*, body: bytes, timestamp: str, nonce: str, signing_secret: str) -> str:
    content = f"{timestamp}{nonce}{signing_secret}".encode() + body
    return hashlib.sha256(content).hexdigest()


def decrypt_feishu_encrypt(encrypt_key: str, encrypted: str) -> str:
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    payload = base64.b64decode(encrypted)
    iv = payload[: AES.block_size]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = _unpad(cipher.decrypt(payload[AES.block_size :]))
    return decrypted.decode("utf-8")


def _unpad(value: bytes) -> bytes:
    padding = value[-1]
    if padding < 1 or padding > AES.block_size:
        return value
    return value[:-padding]
