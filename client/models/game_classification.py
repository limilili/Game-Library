"""游戏-分类关联实体（多维度；v1.3：挂 scheme_id，切换式核心）。"""
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


class GameClassification(BaseKuJiModel, Base):
    __tablename__ = "game_classifications"
    __table_args__ = (
        # 审查修复 L1（2026-08-13）：原约束 (appid, scheme, dimension) 错误阻断
        # RATING/STATUS 多标签（PRD：评价维度可挂多个，如 神作+耐玩）。
        # 现改为：
        # 1) TYPE 维度部分唯一（仅 dimension='TYPE' 时一游戏一方案一行，SQLite 部分索引）
        # 2) 标签维度去重（同一游戏同一方案同一维度同一 tag 不重复）
        Index(
            "uq_classification_type",
            "game_appid", "scheme_id",
            unique=True,
            sqlite_where=text("dimension = 'TYPE'"),
        ),
        UniqueConstraint(
            "game_appid", "scheme_id", "dimension", "tag_id",
            name="uq_classification_tag",
        ),
    )


    scheme_id: Mapped[str] = mapped_column(String(36), ForeignKey("category_schemes.id"), index=True)
    game_appid: Mapped[str] = mapped_column(String(32), ForeignKey("games.appid"), index=True)
    dimension: Mapped[str] = mapped_column(String(16))  # TYPE | RATING | STATUS
    category_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("categories.id"), default=None
    )  # dimension=TYPE 时指向叶节点
    tag_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tags.id"), default=None
    )  # dimension=RATING/STATUS 时
    source: Mapped[str] = mapped_column(String(16), default="MANUAL")  # SYSTEM_PRE_DB|USER_RULE|AI|MANUAL
    confidence: Mapped[Optional[float]] = mapped_column(Float, default=None)  # AI 来源时
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
