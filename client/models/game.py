"""游戏实体（v1.5：source 区分自有/家庭共享）。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class Game(BaseKuJiModel, Base):
    """Steam 游戏。主键用 Steam AppID 字符串（CLAUDE.md 数据库风格例外）。"""

    __tablename__ = "games"
    __table_args__ = (
        # 复合主键 (appid, id) 下 appid 仍可能重复；唯一索引防止逻辑删除后重插
        Index("uq_games_appid", "appid", unique=True),
    )

    appid: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    install_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    library_path: Mapped[Optional[str]] = mapped_column(String(512), index=True, default=None)
    is_installed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="OWNED")  # OWNED | FAMILY_SHARED（v1.5）
    app_type: Mapped[str] = mapped_column(String(32), default="game")  # game|software|dlc|demo|music|video|unknown（2026-08-12 哩米指出工具软件）
    developer: Mapped[Optional[str]] = mapped_column(String(256), default=None)  # 开发商（2026-08-13 哩米需求）
    publisher: Mapped[Optional[str]] = mapped_column(String(256), default=None)  # 发行商
    last_played: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    playtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    steam_tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组字符串
    existing_steam_categories: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True, default=None)
