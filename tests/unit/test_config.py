from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from recebako.config import (
    CONFIG_ENV_VAR,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TEMPERATURE,
    ConfigError,
    OllamaConfig,
    default_config_path,
    default_ollama_config,
    load_config,
    load_ollama_config_for_extract,
    ollama_config_with_model,
    resolve_config_path,
)


def _valid_config(
    data_root: Path,
    *,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    model: str = DEFAULT_OLLAMA_MODEL,
    temperature: Any = DEFAULT_OLLAMA_TEMPERATURE,
) -> str:
    return f"""
[data]
root = {json.dumps(str(data_root))}

[ollama]
base_url = {json.dumps(base_url)}
model = {json.dumps(model)}
temperature = {json.dumps(temperature)}

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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("HTTP://LOCALHOST:11434", "http://127.0.0.1:11434"),
        ("http://LocalHost:43123", "http://127.0.0.1:43123"),
        ("http://127.0.0.1:43123", "http://127.0.0.1:43123"),
        ("http://localhost", "http://127.0.0.1:80"),
        ("http://127.0.0.1/", "http://127.0.0.1:80"),
        ("http://LOCALHOST:11434/", "http://127.0.0.1:11434"),
    ],
)
def test_ollama_base_url_accepts_and_canonicalizes_loopback(
    value: str,
    expected: str,
) -> None:
    config = OllamaConfig(
        base_url=value,
        model=DEFAULT_OLLAMA_MODEL,
        temperature=DEFAULT_OLLAMA_TEMPERATURE,
    )

    assert config.base_url == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:11434",
        "file://localhost/tmp/ollama",
        "http://192.0.2.1:11434",
        "http://example.com:11434",
        "http://localhost.:11434",
        "http://ollama.localhost:11434",
        "http://localhost.example:11434",
        "http://user@localhost:11434",
        "http://user:password@127.0.0.1:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?model=test",
        "http://localhost:11434?",
        "http://localhost:11434#fragment",
        "http://localhost:11434#",
        "http://localhost:11434/?#",
        "http://[::1]:11434",
        "http://127.1:11434",
        "http://2130706433:11434",
        "http://localhost:",
        "http://localhost:invalid",
        "http://localhost:-1",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:11434\\@example.com",
        "http://local\nhost:11434",
        " http://localhost:11434",
        "http://localhost:11434 ",
    ],
)
def test_ollama_base_url_rejects_unsafe_or_non_loopback_values(
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        OllamaConfig(
            base_url=value,
            model=DEFAULT_OLLAMA_MODEL,
            temperature=DEFAULT_OLLAMA_TEMPERATURE,
        )


@pytest.mark.parametrize("model", ["", " ", "\t", 123, None])
def test_ollama_model_rejects_blank_or_non_string_values(model: object) -> None:
    with pytest.raises(ValidationError):
        OllamaConfig.model_validate(
            {
                "base_url": DEFAULT_OLLAMA_BASE_URL,
                "model": model,
                "temperature": DEFAULT_OLLAMA_TEMPERATURE,
            }
        )


@pytest.mark.parametrize("temperature", [-1, 1, False, "0", 0.0, None])
def test_ollama_temperature_requires_strict_integer_zero(
    temperature: object,
) -> None:
    with pytest.raises(ValidationError):
        OllamaConfig.model_validate(
            {
                "base_url": DEFAULT_OLLAMA_BASE_URL,
                "model": DEFAULT_OLLAMA_MODEL,
                "temperature": temperature,
            }
        )


def test_ollama_config_is_frozen() -> None:
    config = default_ollama_config()

    with pytest.raises(ValidationError, match="frozen"):
        config.model = "replacement-model"  # type: ignore[misc]


def test_default_ollama_config_uses_central_values_and_returns_new_instances() -> None:
    first = default_ollama_config()
    second = default_ollama_config()

    assert first == OllamaConfig(
        base_url=DEFAULT_OLLAMA_BASE_URL,
        model=DEFAULT_OLLAMA_MODEL,
        temperature=DEFAULT_OLLAMA_TEMPERATURE,
    )
    assert second == first
    assert second is not first


def test_ollama_config_with_model_preserves_endpoint_temperature_and_base() -> None:
    base = OllamaConfig(
        base_url="http://localhost:43123/",
        model="base-model",
        temperature=DEFAULT_OLLAMA_TEMPERATURE,
    )

    overridden = ollama_config_with_model(base, model="override-model")

    assert overridden is not base
    assert overridden.base_url == "http://127.0.0.1:43123"
    assert overridden.model == "override-model"
    assert overridden.temperature == DEFAULT_OLLAMA_TEMPERATURE
    assert base.model == "base-model"
    assert base.base_url == "http://127.0.0.1:43123"


def test_ollama_config_with_model_revalidates_override() -> None:
    base = default_ollama_config()

    with pytest.raises(ValidationError):
        ollama_config_with_model(base, model=" ")

    assert base == default_ollama_config()


def test_extract_config_explicit_environment_override_has_priority(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(
        _valid_config(
            tmp_path / "implicit-data",
            model="implicit-model",
        ),
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(
        _valid_config(
            tmp_path / "explicit-data",
            base_url="http://LOCALHOST:43123/",
            model="explicit-model",
        ),
        encoding="utf-8",
    )

    resolved = load_ollama_config_for_extract(
        environ={CONFIG_ENV_VAR: str(explicit)},
        home=home,
    )

    assert resolved == OllamaConfig(
        base_url="http://127.0.0.1:43123",
        model="explicit-model",
        temperature=DEFAULT_OLLAMA_TEMPERATURE,
    )


def test_extract_config_explicit_missing_path_does_not_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(
        _valid_config(tmp_path / "implicit-data"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="見つかりません"):
        load_ollama_config_for_extract(
            environ={CONFIG_ENV_VAR: str(tmp_path / "missing.toml")},
            home=home,
        )


def test_extract_config_empty_explicit_override_does_not_fallback(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(
        _valid_config(tmp_path / "implicit-data"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=CONFIG_ENV_VAR):
        load_ollama_config_for_extract(
            environ={CONFIG_ENV_VAR: ""},
            home=home,
        )


@pytest.mark.parametrize(
    "invalid_config",
    [
        "[not valid toml",
        _valid_config(
            Path("/tmp/recebako-explicit-invalid"),
            base_url="http://example.com:11434",
        ),
    ],
)
def test_extract_config_explicit_invalid_file_does_not_fallback(
    tmp_path: Path,
    invalid_config: str,
) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(
        _valid_config(tmp_path / "implicit-data"),
        encoding="utf-8",
    )
    explicit = tmp_path / "invalid.toml"
    explicit.write_text(invalid_config, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_ollama_config_for_extract(
            environ={CONFIG_ENV_VAR: str(explicit)},
            home=home,
        )


def test_extract_config_uses_valid_implicit_default_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(
        _valid_config(
            tmp_path / "implicit-data",
            base_url="http://localhost:43123",
            model="implicit-model",
        ),
        encoding="utf-8",
    )

    resolved = load_ollama_config_for_extract(environ={}, home=home)

    assert resolved.base_url == "http://127.0.0.1:43123"
    assert resolved.model == "implicit-model"
    assert resolved.temperature == DEFAULT_OLLAMA_TEMPERATURE


def test_extract_config_uses_builtin_only_when_implicit_file_is_missing(
    tmp_path: Path,
) -> None:
    resolved = load_ollama_config_for_extract(
        environ={},
        home=tmp_path / "missing-home",
    )

    assert resolved == default_ollama_config()


@pytest.mark.parametrize(
    "invalid_config",
    [
        "[not valid toml",
        _valid_config(
            Path("/tmp/recebako-implicit-invalid"),
            base_url="http://example.com:11434",
        ),
    ],
)
def test_extract_config_existing_invalid_implicit_file_does_not_fallback(
    tmp_path: Path,
    invalid_config: str,
) -> None:
    home = tmp_path / "home"
    implicit = default_config_path(home=home)
    implicit.parent.mkdir(parents=True)
    implicit.write_text(invalid_config, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_ollama_config_for_extract(environ={}, home=home)
