from __future__ import annotations

import getpass
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

SESSION_COOKIE = "teslaslip_session"
CSRF_COOKIE = "teslaslip_login_csrf"
SESSION_DAYS = 7


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("密码长度必须为 12 到 1024 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=5).hex()
    return f"scrypt${salt}${digest}"


def valid_password_hash(encoded: str) -> bool:
    try:
        algorithm, salt, digest = encoded.split("$")
        return algorithm == "scrypt" and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 64
    except ValueError:
        return False


def verify_password(password: str, encoded: str) -> bool:
    if not valid_password_hash(encoded) or not 12 <= len(password) <= 1024:
        return False
    _, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=5).hex()
    return hmac.compare_digest(actual, expected)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def safe_next(value: str) -> str:
    if len(value) > 512 or any(ord(character) < 33 for character in value) or "\\" in value or "%" in value:
        return "/"
    try:
        parts = urlsplit(value)
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc or not value.startswith("/") or value.startswith("//"):
        return "/"
    if parts.path == "/" or parts.path.startswith(("/d/", "/c/", "/r/", "/history/")):
        return value
    return "/"


class Auth:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        settings = runtime.settings
        self.enabled = bool(settings.admin_password_hash)
        self.fingerprint = digest_token(f"{settings.admin_username}:{settings.admin_password_hash}")
        self.secure = urlsplit(settings.public_base_url).scheme == "https"

    def session(self, request) -> str | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        if not self.enabled or len(token) != 43:
            return None
        if self.runtime.store.valid_session(digest_token(token), self.fingerprint, self.runtime.clock.now().timestamp()):
            return token
        return None

    def csrf(self, token: str) -> str:
        return hmac.new(self.fingerprint.encode(), token.encode(), hashlib.sha256).hexdigest()

    def set_cookie(self, response, key: str, value: str, max_age: int) -> None:
        response.set_cookie(key, value, max_age=max_age, httponly=True, secure=self.secure, samesite="lax", path="/")

    def clear_cookie(self, response, key: str) -> None:
        response.delete_cookie(key, httponly=True, secure=self.secure, samesite="lax", path="/")


def main() -> None:
    password = getpass.getpass("管理员密码（至少 12 个字符）：")
    if password != getpass.getpass("再次输入："):
        raise SystemExit("两次输入不一致")
    try:
        print(password_hash(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
