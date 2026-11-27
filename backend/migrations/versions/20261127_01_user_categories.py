"""用户品类权限

Revision ID: 20261127_01
Revises: 20261123_01
Create Date: 2026-11-27

给 users 增加 categories（JSON 数组，存 Category.code）：
- "[]" = 全部品类（默认值，老用户行为不变）
- ["A","B"] = 只能操作这些品类
"""

import sqlalchemy as sa
from alembic import op

revision = "20261127_01"
down_revision = "20261123_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("categories", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("users", "categories")
