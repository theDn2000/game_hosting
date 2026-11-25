"""User authentication fields: role, password hash and active flag.

Revision ID: 0002
Revises: 0001

Existing users keep working: they become regular (`user`), active accounts with
no password (they cannot sign in until an operator sets one). The CHECK values
mirror app.domain.models; tests/integration/test_schema_constraints.py verifies
it. Like every migration, this is a frozen snapshot: add a new revision instead
of editing it once it has been applied anywhere.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("role", sa.Text(), server_default="user", nullable=False)
    )
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_users_role_valid"), "users", "role IN ('user', 'admin')"
    )
    op.create_check_constraint(
        op.f("ck_users_password_hash_valid"),
        "users",
        "password_hash IS NULL OR char_length(password_hash) BETWEEN 1 AND 255",
    )


def downgrade():
    op.drop_constraint(op.f("ck_users_password_hash_valid"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.drop_column("users", "is_active")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "role")
