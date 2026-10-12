"""Short-lived download links for private generated videos."""
import hashlib
import hmac
import time
from urllib.parse import quote


def signature(path: str, expires: int, secret: str) -> str:
    return hmac.new(secret.encode(), f"{path}\n{expires}".encode(), hashlib.sha256).hexdigest()


def signed_url(base: str, filename: str, secret: str, ttl: int = 3600) -> str:
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("Invalid video filename")
    path = f"/videos/{filename}"
    url = f"{base.rstrip('/')}/videos/{quote(filename, safe='')}"
    if not secret:
        return url
    expires = int(time.time()) + ttl
    return f"{url}?expires={expires}&signature={signature(path, expires, secret)}"


def authorized(path: str, expires: str, supplied: str, secret: str) -> bool:
    if not secret:
        return False
    try:
        deadline = int(expires)
    except (TypeError, ValueError):
        return False
    return deadline > time.time() and hmac.compare_digest(signature(path, deadline, secret).encode(), (supplied or "").encode())
