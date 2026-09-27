"""备份记录实体（M3.1：时间戳命名 + 哈希校验）。"""
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class BackupRecord(BaseKuJiModel, Base):
    __tablename__ = "backup_records"

    created_at_ts: Mapped[str] = mapped_column(String(20), index=True)  # 时间戳命名（%Y%m%d%H%M%S）
    file_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))  # 哈希校验（M3.1）
    steam_categories_snapshot: Mapped[str] = mapped_column(Text, default="{}")  # JSON 快照
    game_count: Mapped[int] = mapped_column(Integer, default=0)
    category_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
