import hashlib
import hmac


def verify_hmac(body: bytes, signature: str, secret: str) -> bool:
    if not isinstance(body, (bytes, bytearray)):
        return False
    if not isinstance(signature, str) or not isinstance(secret, str):
        return False
    if not signature or not secret:
        return False
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
