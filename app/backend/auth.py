"""JWT 认证：初始化 / 登录 / 状态 / 改密。

token HS256，TTL 7 天，Subject 固定 'ARTEX'。签名 key 存 data/jwt.key。
Token 来源：Authorization Bearer / artex_token cookie / ?token= 查询参数(SSE)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

import jwt as pyjwt

AUTH_SUBJECT = "ARTEX"
TOKEN_TTL_SECONDS = 7 * 24 * 3600
USERNAME = "ARTEX"


class Auth:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.key_path = os.path.join(data_dir, "jwt.key")
        self._secret: Optional[bytes] = None
        os.makedirs(data_dir, exist_ok=True)
        if not os.path.exists(self.key_path):
            with open(self.key_path, "wb") as f:
                f.write(secrets.token_bytes(48))
        with open(self.key_path, "rb") as f:
            self._secret = f.read()

    # ---- 初始化 / 密码 -------------------------------------------------
    def initialized(self) -> bool:
        return os.path.exists(os.path.join(self.data_dir, "admin_hash"))

    def _hash_path(self) -> str:
        return os.path.join(self.data_dir, "admin_hash")

    def _password_hash(self, password: str) -> str:
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 200_000).hex()
        return f"{salt}${dk}"

    def _password_verify(self, password: str, stored: str) -> bool:
        try:
            salt, dk = stored.split("$", 1)
            calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 200_000).hex()
            return hmac.compare_digest(calc, dk)
        except Exception:
            return False

    def init_password(self, password: str) -> None:
        with open(self._hash_path(), "w") as f:
            f.write(self._password_hash(password))

    def verify_password(self, password: str) -> bool:
        if not self.initialized():
            return False
        with open(self._hash_path()) as f:
            stored = f.read().strip()
        return self._password_verify(password, stored)

    def change_password(self, old: str, new: str) -> bool:
        if not self.verify_password(old):
            return False
        self.init_password(new)
        return True

    # ---- JWT -----------------------------------------------------------
    def sign_token(self) -> str:
        now_ts = int(time.time())
        payload = {
            "sub": AUTH_SUBJECT,
            "iat": now_ts,
            "exp": now_ts + TOKEN_TTL_SECONDS,
        }
        return pyjwt.encode(payload, self._secret, algorithm="HS256")

    def validate_token(self, token: str) -> bool:
        try:
            payload = pyjwt.decode(token, self._secret, algorithms=["HS256"])
            return payload.get("sub") == AUTH_SUBJECT
        except Exception:
            return False


def extract_token(auth: Auth, headers: dict, cookies: dict, query: dict) -> Optional[str]:
    """按 头 -> cookie -> query 顺序取 token。"""
    hdr = headers.get("authorization") or ""
    if str(hdr).lower().startswith("bearer "):
        return str(hdr)[7:]
    tok = cookies.get("artex_token")
    if tok:
        return tok
    tok = query.get("token")
    if tok:
        return tok
    return None
