"""Private, user-scoped storage for optional theme background images."""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import zlib

from ..config import DATA_DIR
from ..models import User

MAX_BACKGROUND_BYTES = 5 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 24_000_000
_SAFE_NAME = re.compile(r"^[a-f0-9]{32}\.(png|jpg|webp)$")


def inspect_background_image(data: bytes) -> tuple[str, str, int, int]:
    """Identify supported raster images from bytes and enforce pixel limits."""
    width = height = 0
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        offset = 8
        seen_ihdr = seen_idat = seen_iend = False
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            chunk_type = data[offset + 4:offset + 8]
            chunk_end = offset + 12 + length
            if chunk_end > len(data):
                raise ValueError("Malformed PNG image")
            payload = data[offset + 8:offset + 8 + length]
            expected_crc = int.from_bytes(data[offset + 8 + length:chunk_end], "big")
            if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
                raise ValueError("PNG integrity check failed")
            if not seen_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    raise ValueError("Malformed PNG header")
                width = int.from_bytes(payload[0:4], "big")
                height = int.from_bytes(payload[4:8], "big")
                seen_ihdr = True
            elif chunk_type == b"IHDR":
                raise ValueError("Malformed PNG: duplicate image header")
            if chunk_type == b"IDAT":
                seen_idat = True
            if chunk_type == b"IEND":
                if length != 0 or chunk_end != len(data):
                    raise ValueError("Malformed PNG terminator")
                seen_iend = True
                break
            offset = chunk_end
        if not (seen_ihdr and seen_idat and seen_iend):
            raise ValueError("Incomplete PNG image")
        extension, media_type = "png", "image/png"
    elif data.startswith(b"\xff\xd8"):
        if not data.endswith(b"\xff\xd9"):
            raise ValueError("Incomplete JPEG image")
        extension, media_type = "jpg", "image/jpeg"
        offset = 2
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in sof and segment_length >= 7:
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                break
            offset += segment_length
    elif len(data) >= 25 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ValueError("Malformed WebP container")
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            if data[20] & 0x02:  # Reject animated backgrounds; no hidden motion.
                raise ValueError("Animated backgrounds are not supported")
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        elif chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            b1, b2, b3, b4 = data[21:25]
            width = 1 + (((b2 & 0x3F) << 8) | b1)
            height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
        else:
            raise ValueError("Unsupported or malformed WebP image")
        extension, media_type = "webp", "image/webp"
    else:
        raise ValueError("Only PNG, JPEG, and WebP raster images are supported")

    if width < 1 or height < 1 or width * height > MAX_BACKGROUND_PIXELS:
        raise ValueError("Image dimensions are invalid or exceed 24 megapixels")
    return extension, media_type, width, height


def user_background_dir(user_id: int) -> Path:
    root = DATA_DIR.resolve() / "private" / "theme-backgrounds"
    private_root = root.parent
    private_root.mkdir(parents=True, exist_ok=True)
    if private_root.resolve() != private_root:
        raise ValueError("Invalid private storage path")
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root:
        raise ValueError("Invalid private storage path")
    path = root / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path:
        raise ValueError("Invalid private storage path")
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass  # POSIX mode bits do not replace the Windows ACL deployment boundary.
    return path


def background_path(user: User) -> Path | None:
    name = user.theme_background_filename
    if not name:
        return None
    if not _SAFE_NAME.fullmatch(name):
        return None
    directory = user_background_dir(user.id)
    candidate = (directory / name).resolve()
    return candidate if candidate.parent == directory and candidate.is_file() else None


def store_background(user_id: int, data: bytes, extension: str) -> str:
    directory = user_background_dir(user_id)
    name = f"{secrets.token_hex(16)}.{extension}"
    target = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return name


def remove_background(user_id: int, name: str | None) -> None:
    if not name or not _SAFE_NAME.fullmatch(name):
        return
    directory = user_background_dir(user_id)
    target = (directory / name).resolve()
    if target.parent == directory:
        target.unlink(missing_ok=True)
