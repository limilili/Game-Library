"""修复 RATING 唯一约束（审查 L1）：TYPE 部分唯一 + 标签去重

Revision ID: 18f25dcc4be0
Revises: c8824cbb911c
Create Date: 2026-08-13 01:17:03.750231

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18f25dcc4be0'
down_revision: Union[str, Sequence[str], None] = 'c8824cbb911c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 审查修复 L1：原 uq_classification_type (appid, scheme, dimension) 错误阻断
    # RATING/STATUS 多标签（PRD：评价维度可挂多个）。
    # SQLite 表级 UNIQUE 约束生成 sqlite_autoindex，无法直接 drop → 整表重建。
    op.rename_table("game_classifications", "_gc_old")
    op.create_table(
        "game_classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("del_flag", sa.Boolean(), nullable=False),
        sa.Column("scheme_id", sa.String(length=36), nullable=False),
        sa.Column("game_appid", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=True),
        sa.Column("tag_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ),
        sa.ForeignKeyConstraint(["game_appid"], ["games.appid"], ),
        sa.ForeignKeyConstraint(["scheme_id"], ["category_schemes.id"], ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_appid", "scheme_id", "dimension", "tag_id",
                            name="uq_classification_tag"),
    )
    # 数据迁移（原表数据保留）
    op.execute(
        "INSERT INTO game_classifications "
        "(id, created_at, updated_at, del_flag, scheme_id, game_appid, dimension, "
        "category_id, tag_id, source, confidence, is_confirmed) "
        "SELECT id, created_at, updated_at, del_flag, scheme_id, game_appid, dimension, "
        "category_id, tag_id, source, confidence, is_confirmed FROM _gc_old"
    )
    op.drop_table("_gc_old")
    # 新约束：TYPE 部分唯一（一游戏一方案一行）+ 标签维度去重（内联于建表）
    op.create_index(
        "uq_classification_type",
        "game_classifications",
        ["game_appid", "scheme_id"],
        unique=True,
        sqlite_where=sa.text("dimension = 'TYPE'"),
    )
    op.create_index("ix_game_classifications_game_appid",
                    "game_classifications", ["game_appid"], unique=False)
    op.create_index("ix_game_classifications_scheme_id",
                    "game_classifications", ["scheme_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema：恢复旧约束（单维度唯一）。"""
    op.rename_table("game_classifications", "_gc_old")
    op.create_table(
        "game_classifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("del_flag", sa.Boolean(), nullable=False),
        sa.Column("scheme_id", sa.String(length=36), nullable=False),
        sa.Column("game_appid", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=True),
        sa.Column("tag_id", sa.String(length=36), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ),
        sa.ForeignKeyConstraint(["game_appid"], ["games.appid"], ),
        sa.ForeignKeyConstraint(["scheme_id"], ["category_schemes.id"], ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_appid", "scheme_id", "dimension",
                            name="uq_classification_type"),
    )
    op.execute(
        "INSERT INTO game_classifications "
        "(id, created_at, updated_at, del_flag, scheme_id, game_appid, dimension, "
        "category_id, tag_id, source, confidence, is_confirmed) "
        "SELECT id, created_at, updated_at, del_flag, scheme_id, game_appid, dimension, "
        "category_id, tag_id, source, confidence, is_confirmed FROM _gc_old"
    )
    op.drop_table("_gc_old")
    op.create_index("ix_game_classifications_game_appid",
                    "game_classifications", ["game_appid"], unique=False)
    op.create_index("ix_game_classifications_scheme_id",
                    "game_classifications", ["scheme_id"], unique=False)
