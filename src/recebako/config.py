from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
ALLOWED_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
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
    model_config = ConfigDict(extra="forbid")

    base_url: StrictStr
    model: StrictStr
    temperature: StrictInt

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if value != ALLOWED_OLLAMA_BASE_URL:
            raise ValueError("許可されたlocalhost URLではありません")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空文字は指定できません")
        return value

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: int) -> int:
        if value != 0:
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
