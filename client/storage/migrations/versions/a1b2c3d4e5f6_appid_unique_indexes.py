"""add appid unique indexes

Revision ID: a1b2c3d4e5f6
Revises: 18f25dcc4be0
Create Date: 2026-09-27

修复 Game / MetadataCache 复合主键下 appid 可重复的问题：
- 先清理历史重复 appid（保留未删除且最新的一条）
- 再创建唯一索引，防止逻辑删除后重新扫描插入重复行
"""
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "18f25dcc4be0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 清理 games 重复：优先保留 del_flag=0，其次保留 rowid 最大（最新插入）
    op.execute("""
        DELETE FROM games
        WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY appid
                           ORDER BY del_flag ASC, rowid DESC
                       ) AS rn
                FROM games
            ) WHERE rn = 1
        )
    """)
    # 清理 metadata_cache 重复：保留 rowid 最大
    op.execute("""
        DELETE FROM metadata_cache
        WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY appid
                           ORDER BY rowid DESC
                       ) AS rn
                FROM metadata_cache
            ) WHERE rn = 1
        )
    """)
    op.create_index("uq_games_appid", "games", ["appid"], unique=True)
    op.create_index("uq_metadata_cache_appid", "metadata_cache", ["appid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_metadata_cache_appid", table_name="metadata_cache")
    op.drop_index("uq_games_appid", table_name="games")
