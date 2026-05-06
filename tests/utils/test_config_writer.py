from grab.utils.config_writer import write_browser_profile_name


def test_write_browser_profile_name_preserves_existing_comments(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """# top comment
auth:
  strategy: "manual"
browser:
  # keep this comment
  stealth: true
""",
        encoding="utf-8",
    )

    write_browser_profile_name(config_path, "profile_2")

    updated = config_path.read_text(encoding="utf-8")
    assert "# top comment" in updated
    assert "# keep this comment" in updated
    assert "profile_name: profile_2" in updated
    assert "stealth: true" in updated


def test_write_browser_profile_name_replaces_file_atomically(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("browser:\n  stealth: true\n", encoding="utf-8")

    write_browser_profile_name(config_path, "profile_9")

    assert config_path.read_text(encoding="utf-8").strip().endswith(
        "profile_name: profile_9"
    )
    assert list(tmp_path.glob(".config.yaml.*.tmp")) == []
