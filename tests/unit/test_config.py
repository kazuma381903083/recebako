from __future__ import annotations

import json
from pathlib import Path

import pytest

from recebako.config import (
    CONFIG_ENV_VAR,
    ConfigError,
    default_config_path,
    load_config,
    resolve_config_path,
)


def _valid_config(data_root: Path) -> str:
    return f"""
[data]
root = {json.dumps(str(data_root))}

[ollama]
base_url = "http://127.0.0.1:11434"
model = "qwen3-vl:8b"
temperature = 0

[review_ui]
host = "127.0.0.1"
port = 8765
"""


def test_load_valid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    data_root = tmp_path / "data"
    config_path.write_text(_valid_config(data_root), encoding="utf-8")

    config = load_config(config_path)

    assert config.data.root == data_root
    assert config.ollama.model == "qwen3-vl:8b"
    assert config.review_ui.port == 8765
    assert config.deduplication.phash_distance_threshold == 5


def test_load_configured_phash_threshold(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _valid_config(tmp_path / "data")
        + """
[deduplication]
phash_distance_threshold = 3
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.deduplication.phash_distance_threshold == 3


def test_default_config_path_uses_home(tmp_path: Path) -> None:
    expected = tmp_path / ".config/recebako/config.toml"
    expected.parent.mkdir(parents=True)
    expected.write_text(_valid_config(tmp_path / "data"), encoding="utf-8")

    assert default_config_path(home=tmp_path) == expected
    assert resolve_config_path(environ={}, home=tmp_path) == expected
    assert load_config(environ={}, home=tmp_path).data.root == tmp_path / "data"


def test_environment_overrides_default_path(tmp_path: Path) -> None:
    override_path = tmp_path / "override.toml"
    override_path.write_text(
        _valid_config(tmp_path / "override-data"),
        encoding="utf-8",
    )

    resolved = resolve_config_path(
        environ={CONFIG_ENV_VAR: str(override_path)},
        home=tmp_path / "unused-home",
    )
    config = load_config(
        environ={CONFIG_ENV_VAR: str(override_path)},
        home=tmp_path / "unused-home",
    )

    assert resolved == override_path
    assert config.data.root == tmp_path / "override-data"


def test_missing_config_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="見つかりません"):
        load_config(tmp_path / "missing.toml")


def test_missing_required_setting_is_reported(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[data]
root = "/tmp/recebako-data"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="ollama"):
        load_config(config_path)


def test_relative_data_root_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _valid_config(Path("relative/data")),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="data.root"):
        load_config(config_path)


def test_non_local_ollama_url_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _valid_config(tmp_path / "data").replace(
            "http://127.0.0.1:11434",
            "http://192.0.2.1:11434",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="ollama.base_url"):
        load_config(config_path)


def test_invalid_setting_type_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _valid_config(tmp_path / "data").replace(
            "port = 8765",
            'port = "8765"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="review_ui.port"):
        load_config(config_path)
