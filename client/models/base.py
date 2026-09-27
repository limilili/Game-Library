"""ORM 基类：DeclarativeBase + 公共字段 mixin（CLAUDE.md 数据库风格）。"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """UTC naive 时间戳（SQLite DateTime 不支持 tz-aware）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class BaseKuJiModel:
    """公共字段：UUID 主键 + created_at/updated_at 自动维护 + del_flag 逻辑删除。"""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    del_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dict(self) -> dict[str, Any]:
        """序列化（datetime → ISO 8601 字符串，CLAUDE.md 规范）。"""
        result: dict[str, Any] = {}
        for col in self.__table__.columns:
            value = getattr(self, col.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[col.name] = value
        return result
