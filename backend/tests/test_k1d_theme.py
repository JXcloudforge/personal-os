import struct
import subprocess
import zlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import User
from app.services import theme_service


def _tiny_png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x20\x10\xff")) + chunk(b"IEND", b"")


@pytest.fixture(autouse=True)
def isolated_theme_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_service, "DATA_DIR", tmp_path / "data")
    Base.metadata.create_all(bind=engine)


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("personal_os_csrf")}


def _client(username: str, password: str):
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return client


def test_theme_preference_is_authenticated_persistent_and_validated():
    assert TestClient(app).get("/api/account/appearance").status_code == 401
    client = _client("theme-user", "Theme-Password-2026!")
    try:
        initial = client.get("/api/account/appearance").json()
        assert initial == {"theme": "classic", "has_background": False, "background_url": None, "background_darkness": 48, "background_blur": 3, "background_opacity": 48}
        assert client.patch("/api/account/appearance", json={"theme": "cyberpunk-night"}).status_code == 403
        changed = client.patch("/api/account/appearance", headers=_csrf(client), json={"theme": "cyberpunk-night", "background_darkness": 81, "background_blur": 7, "background_opacity": 44})
        assert changed.status_code == 200
        assert client.get("/api/account/appearance").json()["theme"] == "cyberpunk-night"
        assert changed.json()["background_darkness"] == 81
        assert client.patch("/api/account/appearance", headers=_csrf(client), json={"theme": "neon"}).status_code == 422
        assert client.patch("/api/account/appearance", headers=_csrf(client), json={"background_blur": 99}).status_code == 422
    finally:
        client.close()


def test_tailnet_https_auth_cookies_and_origin_remain_secure():
    username = "theme-https-user"
    password = "Theme-Password-2026!"
    with SessionLocal() as db:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    client = TestClient(app, base_url="https://testserver")
    headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "personal-os.example.ts.net",
        "Origin": "https://personal-os.example.ts.net",
    }
    try:
        response = client.post("/api/auth/login", headers=headers, json={"username": username, "password": password})
        assert response.status_code == 200
        set_cookies = response.headers.get_list("set-cookie")
        session_cookie = next(value.lower() for value in set_cookies if value.startswith("personal_os_session="))
        csrf_cookie = next(value.lower() for value in set_cookies if value.startswith("personal_os_csrf="))
        assert "secure" in session_cookie and "httponly" in session_cookie and "samesite=lax" in session_cookie
        assert "secure" in csrf_cookie and "httponly" not in csrf_cookie and "samesite=lax" in csrf_cookie
        updated = client.patch("/api/account/appearance", headers={**headers, **_csrf(client)}, json={"theme": "cyberpunk-night"})
        assert updated.status_code == 200
    finally:
        client.close()


def test_background_upload_replacement_deletion_and_user_isolation():
    png = _tiny_png()
    first = _client("theme-owner", "Theme-Password-2026!")
    second = _client("theme-other", "Theme-Password-2026!")
    try:
        response = first.put("/api/account/appearance/background", headers=_csrf(first), files={"background": ("../../private.svg", png, "image/svg+xml")})
        assert response.status_code == 200, response.text
        assert response.json()["has_background"] is True
        fetched = first.get("/api/account/appearance/background")
        assert fetched.status_code == 200 and fetched.content == png
        assert fetched.headers["cache-control"] == "private, no-store"
        assert fetched.headers["x-content-type-options"] == "nosniff"
        assert second.get("/api/account/appearance").json()["has_background"] is False
        assert second.get("/api/account/appearance/background").status_code == 404
        assert second.delete("/api/account/appearance/background", headers=_csrf(second)).status_code == 200
        assert first.get("/api/account/appearance/background").content == png
        with SessionLocal() as db:
            first_user_id = db.scalar(select(User.id).where(User.username == "theme-owner"))
        first_user_dir = theme_service.user_background_dir(first_user_id)
        response = first.put("/api/account/appearance/background", headers=_csrf(first), files={"background": ("replacement.png", png, "image/png")})
        assert response.status_code == 200
        assert len(list(first_user_dir.iterdir())) == 1
        oversized = first.put("/api/account/appearance/background", headers=_csrf(first), files={"background": ("large.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")})
        assert oversized.status_code == 413
        invalid = first.put("/api/account/appearance/background", headers=_csrf(first), files={"background": ("bad.png", b"<svg/onload=alert(1)>", "image/png")})
        assert invalid.status_code == 415
        removed = first.delete("/api/account/appearance/background", headers=_csrf(first))
        assert removed.status_code == 200 and not removed.json()["has_background"]
        assert first.get("/api/account/appearance/background").status_code == 404
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("redirect_location", ["private-root", "storage-root", "user-directory"])
def test_theme_storage_rejects_directory_symlink_redirection(tmp_path, monkeypatch, redirect_location):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(theme_service, "DATA_DIR", data_dir)
    root = data_dir / "private" / "theme-backgrounds"
    outside = tmp_path / "outside"
    outside.mkdir()
    if redirect_location == "private-root":
        data_dir.mkdir(parents=True)
        link, target = data_dir / "private", outside
    elif redirect_location == "storage-root":
        (data_dir / "private").mkdir(parents=True)
        link, target = root, outside
    else:
        (root / "202").mkdir(parents=True)
        link, target = root / "101", root / "202"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows commonly disallows ordinary symlink creation for test users;
        # an NTFS junction exercises the same resolved-path boundary.
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True, text=True)

    with pytest.raises(ValueError, match="Invalid private storage path"):
        theme_service.user_background_dir(101)
