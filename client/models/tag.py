"""评价/状态标签实体（v1.3：挂 scheme_id）。"""
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class Tag(BaseKuJiModel, Base):
    __tablename__ = "tags"

    scheme_id: Mapped[str] = mapped_column(String(36), ForeignKey("category_schemes.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(16))  # RATING | STATUS
    description: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    source: Mapped[str] = mapped_column(String(16), default="OFFICIAL")  # OFFICIAL | USER_CREATED
    color: Mapped[Optional[str]] = mapped_column(String(16), default=None)  # hex
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
