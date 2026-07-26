from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

CONFIG_ENV_VAR = "RECEBAKO_CONFIG"
DEFAULT_CONFIG_RELATIVE_PATH = Path(".config/recebako/config.toml")
DEFAULT_OLLAMA_PORT = 11434
DEFAULT_OLLAMA_BASE_URL = f"http://127.0.0.1:{DEFAULT_OLLAMA_PORT}"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:8b"
DEFAULT_OLLAMA_TEMPERATURE = 0
ALLOWED_OLLAMA_BASE_URL = DEFAULT_OLLAMA_BASE_URL
ALLOWED_REVIEW_HOST = "127.0.0.1"


class ConfigError(RuntimeError):
    """設定ファイルを安全に読み込めなかったことを表す。"""


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path

    @field_validator("root")
    @classmethod
    def validate_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("絶対パスで指定してください")
        return value


class OllamaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: StrictStr
    model: StrictStr
    temperature: StrictInt

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if (
            value != value.strip()
            or "\\" in value
            or "?" in value
            or "#" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("許可されたlocalhost URLではありません")
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError("許可されたlocalhost URLではありません") from exc
        if (
            parsed.scheme.lower() != "http"
            or host not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is None and ":" in parsed.netloc)
        ):
            raise ValueError("許可されたlocalhost URLではありません")
        resolved_port = 80 if port is None else port
        if resolved_port < 1:
            raise ValueError("許可されたlocalhost URLではありません")
        return f"http://127.0.0.1:{resolved_port}"

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空文字は指定できません")
        return value

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: int) -> int:
        if value != DEFAULT_OLLAMA_TEMPERATURE:
            raise ValueError("抽出処理のtemperatureは0のみ指定できます")
        return value


class ReviewUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: StrictStr
    port: StrictInt = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if value != ALLOWED_REVIEW_HOST:
            raise ValueError("review UIは127.0.0.1のみ指定できます")
        return value


class DeduplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phash_distance_threshold: StrictInt = Field(default=5, ge=0, le=64)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: DataConfig
    ollama: OllamaConfig
    review_ui: ReviewUIConfig
    deduplication: DeduplicationConfig = Field(default_factory=DeduplicationConfig)


def default_ollama_config() -> OllamaConfig:
    return OllamaConfig(
        base_url=DEFAULT_OLLAMA_BASE_URL,
        model=DEFAULT_OLLAMA_MODEL,
        temperature=DEFAULT_OLLAMA_TEMPERATURE,
    )


def ollama_config_with_model(
    base_config: OllamaConfig,
    *,
    model: str,
) -> OllamaConfig:
    return OllamaConfig(
        base_url=base_config.base_url,
        model=model,
        temperature=base_config.temperature,
    )


def default_config_path(*, home: Path | None = None) -> Path:
    return (home if home is not None else Path.home()) / DEFAULT_CONFIG_RELATIVE_PATH


def resolve_config_path(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get(CONFIG_ENV_VAR)
    if override is not None:
        if not override:
            raise ConfigError(f"{CONFIG_ENV_VAR}が空です")
        return Path(override)
    return default_config_path(home=home)


def load_config(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> AppConfig:
    config_path = (
        path if path is not None else resolve_config_path(environ=environ, home=home)
    )

    try:
        with config_path.open("rb") as config_file:
            raw_config: dict[str, Any] = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError("設定ファイルが見つかりません") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("設定ファイルのTOML形式が不正です") from exc
    except OSError as exc:
        raise ConfigError("設定ファイルを読み込めません") from exc

    try:
        return AppConfig.model_validate(raw_config)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise ConfigError(f"設定項目 '{location}' が不足しているか不正です") from exc


def load_ollama_config_for_extract(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> OllamaConfig:
    """Resolve standalone extraction without breaking its config-free default."""

    environment = os.environ if environ is None else environ
    config_path = resolve_config_path(environ=environment, home=home)
    if CONFIG_ENV_VAR not in environment:
        try:
            config_path.lstat()
        except FileNotFoundError:
            return default_ollama_config()
        except OSError:
            pass
    return load_config(config_path, environ=environment, home=home).ollama
