"""分类方案实体（v1.3 新增：切换式方案，官方/自己/其他三档）。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class CategoryScheme(BaseKuJiModel, Base):
    __tablename__ = "category_schemes"

    name: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="OFFICIAL")  # OFFICIAL | USER | OTHER
    description: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # 全局唯一激活
    version: Mapped[Optional[str]] = mapped_column(String(32), default=None)  # 官方方案 M5 版本戳
    copied_from_scheme_id: Mapped[Optional[str]] = mapped_column(String(36), default=None)
