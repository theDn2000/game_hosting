"""Sessions and failed sign-in attempts.

Revision ID: 0003
Revises: 0002

`sessions` holds server-side sign-in sessions. Only the SHA-256 hash of the
session token is stored, so a leaked row cannot be used to impersonate anyone.
`login_failures` records recent failed sign-ins (keyed by a hash of the e-mail
that was tried and a client key such as the IP address) so that repeated
attempts can be throttled. Both live in PostgreSQL rather than in process
memory so that several API processes share the same state.

Like every migration, this is a frozen snapshot: add a new revision instead of
editing it once it has been applied anywhere.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_sessions_token_hash_format")
        ),
        sa.CheckConstraint(
            "last_seen_at >= created_at", name=op.f("ck_sessions_last_seen_after_created")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_sessions_expires_after_created")
        ),
    )
    op.create_index(
        op.f("ix_sessions_user_id_created_at"), "sessions", ["user_id", "created_at"]
    )
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])
    op.create_index(op.f("ix_sessions_revoked_at"), "sessions", ["revoked_at"])

    op.create_table(
        "login_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email_hash", sa.Text(), nullable=False),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_failures")),
        sa.CheckConstraint(
            "email_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_login_failures_email_hash_format")
        ),
        sa.CheckConstraint(
            "char_length(client_key) BETWEEN 1 AND 64",
            name=op.f("ck_login_failures_client_key_length"),
        ),
    )
    op.create_index(
        op.f("ix_login_failures_email_hash_client_key_occurred_at"),
        "login_failures",
        ["email_hash", "client_key", "occurred_at"],
    )
    op.create_index(
        op.f("ix_login_failures_client_key_occurred_at"),
        "login_failures",
        ["client_key", "occurred_at"],
    )
    op.create_index(op.f("ix_login_failures_occurred_at"), "login_failures", ["occurred_at"])


def downgrade():
    op.drop_table("login_failures")  # its indexes are dropped with it
    op.drop_table("sessions")
