import importlib.util
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

import config
import server
from browser_pool import BrowserPool
from store import TaskStore
from video_links import signature
from media import PublicResolver, validate_reference_urls


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PRODUCTION", True)
    monkeypatch.setattr(config, "ADMIN_KEY", "admin-test-" + "a" * 32)
    monkeypatch.setattr(config, "VIDEO_SIGNING_KEY", "video-test-" + "b" * 32)
    monkeypatch.setattr(config, "API_KEYS", [])
    monkeypatch.setattr(config, "PUBLIC_BASE", "https://video.example.com")
    monkeypatch.setattr(config, "MAX_PENDING_TASKS", 20)
    monkeypatch.setattr(server, "SUPPORTED_DURATIONS", (10, 15))
    store = TaskStore(str(tmp_path / "tasks.db"))
    accounts = tmp_path / "accounts"
    (accounts / "test-account").mkdir(parents=True)
    pool = BrowserPool(accounts_dir=str(accounts), db_path=str(tmp_path / "pool.db"), max_concurrency=1)
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server, "pool", pool)

    async def queued_only(*args, **kwargs):
        pass  # Never call Dola in automated tests.

    monkeypatch.setattr(server, "_run_task", queued_only)
    with TestClient(server.app) as client:
        yield client, store
    pool._conn.close()
    store._conn.close()


def key(store, name, **kwargs):
    return store.create_key(name, allowed_durations=[10, 15], **kwargs)["key"]


def bearer(value):
    return {"Authorization": "Bearer " + value}


def submit(client, value, **extra):
    return client.post("/v1/videos/generations", json={"prompt": "A product on a table", "duration": 10, **extra}, headers=bearer(value))


def test_public_readiness_does_not_expose_accounts(service):
    client, _ = service
    assert client.get("/ready").json() == {"ok": True, "version": "0.5.0"}
    assert client.get("/health").status_code == 401
    assert client.get("/api/admin/accounts").status_code == 401
    assert client.get("/health", headers={"X-Admin-Key": config.ADMIN_KEY}).status_code == 200


def test_production_never_opens_anonymous_api_when_keys_missing(service):
    client, _ = service
    assert client.post("/v1/videos/generations", json={"prompt": "test"}).status_code == 503


def test_admin_can_create_team_key_but_cannot_send_google_credentials(service):
    client, store = service
    response = client.post("/api/admin/keys", headers={"X-Admin-Key": config.ADMIN_KEY},
                           json={"name": "operator", "daily_limit": 2, "allowed_durations": [10, 15]})
    assert response.status_code == 200
    assert store.is_key_valid(response.json()["created"]["key"])
    response = client.post("/api/admin/accounts", headers={"X-Admin-Key": config.ADMIN_KEY},
                           json={"name": "account", "email": "example@example.com", "password": "placeholder", "totp": ""})
    assert response.status_code == 403
    assert not server.JOBS


def test_task_ownership_and_private_video_download(service):
    client, store = service
    alpha, beta = key(store, "alpha"), key(store, "beta")
    response = submit(client, alpha)
    assert response.status_code == 200
    task_id = response.json()["id"]
    assert client.get(f"/v1/videos/{task_id}", headers=bearer(beta)).status_code == 404
    filename = "test-private-download.mp4"
    video = Path(config.DOWNLOAD_DIR) / filename
    video.write_bytes(b"test-video")
    try:
        store.update(task_id, status="completed", video_url=f"https://video.example.com/videos/{filename}")
        result = client.get(f"/v1/videos/{task_id}", headers=bearer(alpha)).json()
        parsed = urlsplit(result["video_url"])
        assert client.get(parsed.path).status_code == 401
        downloaded = client.get(parsed.path + "?" + parsed.query)
        assert downloaded.status_code == 200 and downloaded.content == b"test-video"
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert client.get("/videos/other.mp4?" + parsed.query).status_code == 401
        assert client.get(parsed.path + "?expires=9999999999&signature=bad").status_code == 401
        expired = int(time.time()) - 5
        token = signature(parsed.path, expired, config.VIDEO_SIGNING_KEY)
        assert client.get(f"{parsed.path}?expires={expired}&signature={token}").status_code == 401
    finally:
        video.unlink()


def test_limits_and_key_revocation(service):
    client, store = service
    value = key(store, "limited", daily_limit=1)
    assert submit(client, value).status_code == 200
    assert submit(client, value).status_code == 429
    store.update_key(value, enabled=0)
    assert submit(client, value).status_code == 401
    expired = key(store, "expired", expires_at=time.time() - 1)
    assert submit(client, expired).status_code == 401


def test_global_queue_cap_and_disabled_extension(service, monkeypatch):
    client, store = service
    value = key(store, "operator")
    monkeypatch.setattr(config, "MAX_PENDING_TASKS", 1)
    assert submit(client, value, duration=30).status_code == 422
    assert submit(client, value).status_code == 200
    assert submit(client, value).status_code == 429


def test_tasks_and_keys_survive_database_reopen(tmp_path):
    path = str(tmp_path / "persist.db")
    first = TaskStore(path)
    value = key(first, "persistent")
    first.create("video_" + "c" * 32, "seedance-2.0", "test", "9:16", 10, api_key_hash=first.hash_api_key(value), api_key_name="persistent")
    first._conn.close()
    second = TaskStore(path)
    assert second.is_key_valid(value)
    assert second.get_for_client("video_" + "c" * 32, second.hash_api_key(value))
    assert second.pending_task_count() == 1
    second._conn.close()


def test_production_config_rejects_missing_secrets_and_bad_public_url():
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOLA_")}
    env.update(DOLA_ENV="production", DOLA_PUBLIC_BASE="https://video.example.com")
    command = [sys.executable, "-c", "import config"]
    assert subprocess.run(command, env=env, capture_output=True).returncode != 0
    env.update(DOLA_ADMIN_KEY="a" * 40, DOLA_VIDEO_SIGNING_KEY="b" * 40)
    assert subprocess.run(command, env=env, capture_output=True).returncode == 0
    env["DOLA_PUBLIC_BASE"] = "http://video.example.com"
    assert subprocess.run(command, env=env, capture_output=True).returncode != 0


def test_configuration_is_private_and_cannot_overwrite_existing_secrets(tmp_path):
    spec = importlib.util.spec_from_file_location("deployment_configuration", "deploy/configure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / ".env.production"
    module.configure("video.example.com", "", target)
    assert target.stat().st_mode & 0o777 == 0o600
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        module.configure("video.example.com", "", target)
    assert target.read_bytes() == original
    with pytest.raises(ValueError):
        module.configure("https://invalid.example.com", "", tmp_path / "another")


def test_studio_is_available_and_legacy_dashboard_redirects(service):
    client, _ = service
    assert "Dola Video Studio" in client.get("/studio").text
    assert client.get("/web", follow_redirects=False).status_code == 307
    assert client.get("/capabilities").json() == {"durations": [10, 15]}


def test_non_ascii_credentials_are_rejected_without_server_errors(service):
    client, _ = service
    assert client.post("/api/admin/login", json={"key": "mật-khẩu-sai"}).status_code == 401
    assert client.get("/videos/example.mp4?expires=9999999999&signature=%C3%A9").status_code == 401


def test_reference_images_reject_private_ips_and_dns_rebinding():
    with pytest.raises(ValueError):
        asyncio.run(validate_reference_urls(["http://127.0.0.1/image.png"]))

    class ChangedDNS:
        async def resolve(self, *args):
            return [{"host": "169.254.169.254"}]

        async def close(self):
            pass

    with pytest.raises(ValueError):
        asyncio.run(PublicResolver(ChangedDNS()).resolve("public-image.example.com", 443))
