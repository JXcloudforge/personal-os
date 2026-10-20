from app.release_history import current_app_version, load_release_history


def test_release_history_separates_published_versions_from_roadmap():
    history = load_release_history()

    assert current_app_version() == "0.4.0"
    assert history["candidate"]["version"] == "0.4.0"
    assert history["candidate"]["status"] == "source_candidate"
    assert [item["version"] for item in history["releases"]] == ["0.3.3", "0.3.2", "0.3.1", "0.3.0", "0.2.0", "0.1.0"]
    assert history["releases"][1]["release_date"] == "2026-10-14"
    assert len(history["releases"][1]["lessons"]) == 5
    assert [item["version"] for item in history["roadmap"]] == ["0.5.0", "0.6.0", "0.7.0"]
    assert all(item["status"] == "planned" for item in history["roadmap"])
    assert any("私有主题背景图" in item for item in history["releases"][2]["limitations"])
    assert any("异地自动备份" in item for item in history["releases"][3]["limitations"])
