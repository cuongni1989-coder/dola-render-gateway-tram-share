"""Create private server configuration without printing secrets."""
import argparse
import os
import re
import secrets
from pathlib import Path


def configure(domain: str, proxy: str, target: Path):
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", domain):
        raise ValueError("Enter a domain such as video.example.com, without https:// or a path")
    if any(c in proxy for c in "\r\n"):
        raise ValueError("Proxy must be a single-line URL")
    values = {
        "DOLA_ENV": "production",
        "DOLA_DOMAIN": domain,
        "DOLA_PUBLIC_BASE": f"https://{domain}",
        "DOLA_ADMIN_KEY": secrets.token_urlsafe(32),
        "DOLA_VIDEO_SIGNING_KEY": secrets.token_urlsafe(32),
        "DOLA_VNC_PASSWORD": secrets.token_urlsafe(6),
        "DOLA_PROXY": proxy,
        "DOLA_API_KEYS": "",
        "DOLA_MAX_CONCURRENCY": "1",
        "DOLA_MAX_PENDING_TASKS": "20",
        "DOLA_EXTENSION_ENABLED": "0",
        "DOLA_MANUAL_VERIFY_TIMEOUT": "180",
    }
    # Exclusive creation prevents accidental rotation of an existing server's credentials.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        for key, value in values.items():
            # Single quotes suppress Docker Compose interpolation of dollar signs in URLs.
            output.write(f"{key}='{value.replace(chr(39), chr(92) + chr(39))}'\n")
    print(f"Created {target}. Read credentials privately on the server; do not commit or paste this file.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("--proxy", default="", help="Proxy reachable from inside Docker, if your network requires it")
    args = parser.parse_args()
    configure(args.domain, args.proxy, Path(".env.production"))
