import hashlib
import hmac

from app.utils.hmac_verify import verify_hmac


def _sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    body = b'{"action":"opened"}'
    secret = "dev_secret"
    assert verify_hmac(body, _sig(body, secret), secret)


def test_invalid_signature_fails():
    body = b'{"action":"opened"}'
    assert not verify_hmac(body, "sha256=deadbeef", "dev_secret")
    assert not verify_hmac(body, "not-a-signature", "dev_secret")
