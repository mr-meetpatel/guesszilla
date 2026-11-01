"""Reserve a revision used by the withdrawn database-backed OAuth settings.

Keeping the identifier allows installations that already ran the withdrawn
revision to upgrade. New installations do not create credential storage.
"""

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
