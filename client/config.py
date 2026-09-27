"""配置：pydantic-settings 加载 config.yaml（%APPDATA%/KuJi/config.yaml，缺省用默认值）。

敏感字段（steam_api_key / ai_api_key）落盘时 AES 加密（client.utils.crypto）。
"""
import os
from pathlib import Path
from typing import Any, Type

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from client.utils.crypto import decrypt_secret, encrypt_secret

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "KuJi"
CONFIG_PATH = APP_DIR / "config.yaml"


class Settings(BaseSettings):
    """库集配置（默认值即出厂态，config.yaml 可覆盖）。"""

    model_config = SettingsConfigDict(extra="ignore")

    # Steam
    steam_id: str = ""  # 可选 SteamID（本地账号检测失败/需要指定账号时的补充）
    steam_api_key_enc: str = ""  # AES 加密后的 Key（不入明文）
    steamgriddb_key_enc: str = ""  # SteamGridDB 封面 API Key（AES 加密）
    api_rate_limit_ms: int = 200  # Steam API 限速（PRD 约定）
    api_timeout_s: float = 15.0
    api_retries: int = 2
    proxy: str = ""  # 可选代理（国内网络，POC-5 实测需求）
    safety_notice_shown: bool = False  # 首次写回使用须知是否已确认

    # AI（Phase 2 预留）
    ai_api_key_enc: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_confidence_threshold: float = 0.8

    # 数据库
    db_path: str = ""  # 空 = %APPDATA%/KuJi/kuji.db

    @property
    def resolved_db_path(self) -> Path:
        return Path(self.db_path) if self.db_path else APP_DIR / "kuji.db"

    # ── API Key 安全读写 ────────────────────────────────────────
    @property
    def steam_api_key(self) -> str:
        return decrypt_secret(self.steam_api_key_enc) if self.steam_api_key_enc else ""

    def set_steam_api_key(self, key: str) -> None:
        """设置 Key（明文只存内存，落盘即加密）。"""
        self.steam_api_key_enc = encrypt_secret(key) if key else ""
        self._persist()

    @property
    def steamgriddb_key(self) -> str:
        return decrypt_secret(self.steamgriddb_key_enc) if self.steamgriddb_key_enc else ""

    def set_steamgriddb_key(self, key: str) -> None:
        self.steamgriddb_key_enc = encrypt_secret(key) if key else ""
        self._persist()

    @property
    def ai_api_key(self) -> str:
        return decrypt_secret(self.ai_api_key_enc) if self.ai_api_key_enc else ""

    def set_ai_api_key(self, key: str) -> None:
        self.ai_api_key_enc = encrypt_secret(key) if key else ""
        self._persist()

    # ── 持久化 ──────────────────────────────────────────────────
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """注册 YAML 源：config.yaml 覆盖默认值（优先级 默认 < YAML < 环境变量）。"""
        return (
            init_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=str(CONFIG_PATH)),
            env_settings,
            file_secret_settings,
        )

    def _persist(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        # 只落盘安全字段（用模型 dump）
        from pydantic import TypeAdapter

        adapter = TypeAdapter(dict)
        data = adapter.dump_python(self.model_dump(exclude={"db_path"}))
        import yaml

        CONFIG_PATH.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )


def get_settings() -> Settings:
    return Settings()


def ensure_config_file() -> Settings:
    """首次启动时生成默认 config.yaml（哩米 2026-09-27：干净环境验收发现）。"""
    settings = Settings()
    if not CONFIG_PATH.exists():
        settings._persist()
    return settings
