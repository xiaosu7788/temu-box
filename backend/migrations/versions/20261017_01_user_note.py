"""Add private administrator note to users."""
from alembic import op
import sqlalchemy as sa

revision = "20261017_01"
down_revision = "20261013_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "note" not in columns:
        op.add_column("users", sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "note" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("note")
