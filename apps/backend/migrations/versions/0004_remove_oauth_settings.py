"""Remove storage from the withdrawn database-backed OAuth configuration."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wordle_bot.app_settings")
    op.execute("ALTER TABLE wordle_bot.oauth_states DROP COLUMN IF EXISTS encrypted_client")


def downgrade() -> None:
    # Removed credentials are deliberately not reconstructed.
    pass
