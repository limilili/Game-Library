"""元数据缓存实体（30 天过期策略）。"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from client.models.base import Base, BaseKuJiModel


def default_expires() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)


class MetadataCache(BaseKuJiModel, Base):
    __tablename__ = "metadata_cache"
    __table_args__ = (
        Index("uq_metadata_cache_appid", "appid", unique=True),
    )

    appid: Mapped[str] = mapped_column(String(32), primary_key=True)
    developer: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    publisher: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    release_date: Mapped[Optional[datetime]] = mapped_column(Date, default=None)
    store_tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    genres: Mapped[str] = mapped_column(Text, default="[]")
    review_score: Mapped[Optional[int]] = mapped_column(Integer, default=None)  # 好评率 %
    review_count: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    supported_languages: Mapped[str] = mapped_column(Text, default="[]")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=default_expires)
