"""类型树节点实体（v1.3：挂 scheme_id，方案内树结构）。"""
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class Category(BaseKuJiModel, Base):
    __tablename__ = "categories"

    scheme_id: Mapped[str] = mapped_column(String(36), ForeignKey("category_schemes.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(128), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("categories.id"), default=None)
    level: Mapped[int] = mapped_column(Integer, default=0)  # 根=0
    path: Mapped[str] = mapped_column(String(512), default="")  # "视觉小说/Galgame"
    description: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    icon: Mapped[Optional[str]] = mapped_column(String(32), default=None)  # emoji
    source: Mapped[str] = mapped_column(String(16), default="OFFICIAL")  # OFFICIAL | USER_CREATED
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=False)  # 游戏只能挂叶节点
