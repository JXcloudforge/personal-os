from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import APP_ROOT


RELEASE_HISTORY_PATH = APP_ROOT / "release_history.json"


@lru_cache(maxsize=1)
def load_release_history() -> dict[str, Any]:
    """Read the human-maintained product release source, never infer releases from code."""
    data = json.loads(RELEASE_HISTORY_PATH.read_text(encoding="utf-8"))
    current_version = data.get("current_version")
    releases = data.get("releases")
    roadmap = data.get("roadmap")
    candidate = data.get("candidate")
    if not isinstance(current_version, str) or not isinstance(releases, list) or not isinstance(roadmap, list):
        raise ValueError("Release history must define current_version, releases and roadmap")
    released_versions = {item.get("version") for item in releases}
    candidate_version = candidate.get("version") if isinstance(candidate, dict) else None
    if current_version not in released_versions and current_version != candidate_version:
        raise ValueError("Release history current_version must identify a published release or release candidate")
    if released_versions.intersection(item.get("version") for item in roadmap):
        raise ValueError("A version cannot be both published and planned")
    if any(item.get("status") not in {"planned", "in_development"} for item in roadmap):
        raise ValueError("Roadmap entries must have a known non-released status")
    return data


def current_app_version() -> str:
    return load_release_history()["current_version"]
