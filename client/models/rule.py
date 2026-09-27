"""分类规则实体（v1.3：规则属于某方案，切换方案即切换规则范围）。"""
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class ClassificationRule(BaseKuJiModel, Base):
    __tablename__ = "classification_rules"

    scheme_id: Mapped[str] = mapped_column(String(36), ForeignKey("category_schemes.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    dimension: Mapped[str] = mapped_column(String(16), default="TYPE")  # TYPE | RATING | STATUS
    target_category_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("categories.id"), default=None
    )
    target_tag_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tags.id"), default=None
    )
    conditions: Mapped[str] = mapped_column(Text, default="{}")  # JSON（含 schema_version）
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 数字越小越优先
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="USER_CREATED")  # OFFICIAL | USER_CREATED
