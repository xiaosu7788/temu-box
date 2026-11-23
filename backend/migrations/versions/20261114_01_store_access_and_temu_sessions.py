"""Add store_access flag and Temu store session tables.

店铺管理模块（可选）：
- users.store_access：管理员按用户开放模块入口
- temu_sessions：一用户一店铺的会话绑定（单账号单店铺）
- temu_credentials：勾选「记住密码」时的 AES-GCM 密文
- temu_login_logs：登录/退出/失效事件
"""
from alembic import op
import sqlalchemy as sa

revision = "20261114_01"
down_revision = "20261027_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "store_access" not in columns:
        op.add_column(
            "users",
            sa.Column("store_access", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if not inspector.has_table("temu_sessions"):
        op.create_table(
            "temu_sessions",
            sa.Column("user_id", sa.Integer(), primary_key=True),
            sa.Column("account", sa.String(160), nullable=False, server_default=""),
            sa.Column("account_hash", sa.String(40), nullable=False, server_default=""),
            sa.Column("account_masked", sa.String(120), nullable=False, server_default=""),
            sa.Column("mall_id", sa.String(64), nullable=True),
            sa.Column("mall_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("mall_list", sa.Text(), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="logged_out"),
            sa.Column("remember", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_code", sa.String(40), nullable=False, server_default=""),
            sa.Column("last_error", sa.String(255), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_temu_sessions_account_hash", "temu_sessions", ["account_hash"])

    if not inspector.has_table("temu_credentials"):
        op.create_table(
            "temu_credentials",
            sa.Column("user_id", sa.Integer(), primary_key=True),
            sa.Column("account", sa.String(160), nullable=False, server_default=""),
            sa.Column("ciphertext", sa.Text(), nullable=True),
            sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not inspector.has_table("temu_login_logs"):
        op.create_table(
            "temu_login_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("account_masked", sa.String(120), nullable=False, server_default=""),
            sa.Column("action", sa.String(32), nullable=False, server_default=""),
            sa.Column("code", sa.String(40), nullable=False, server_default=""),
            sa.Column("message", sa.String(255), nullable=False, server_default=""),
            sa.Column("ip", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_temu_login_logs_action", "temu_login_logs", ["action"])
        op.create_index("ix_temu_login_logs_created_at", "temu_login_logs", ["created_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if inspector.has_table("temu_login_logs"):
        op.drop_index("ix_temu_login_logs_created_at", table_name="temu_login_logs")
        op.drop_index("ix_temu_login_logs_action", table_name="temu_login_logs")
        op.drop_table("temu_login_logs")

    if inspector.has_table("temu_credentials"):
        op.drop_table("temu_credentials")

    if inspector.has_table("temu_sessions"):
        op.drop_index("ix_temu_sessions_account_hash", table_name="temu_sessions")
        op.drop_table("temu_sessions")

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "store_access" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("store_access")
