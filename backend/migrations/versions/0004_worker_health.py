"""Worker heartbeat for ingestion diagnostics."""
from alembic import op
import sqlalchemy as sa

revision = '0004_worker_health'
down_revision = '0003_questions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('worker_heartbeats',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('idle','busy','stopped')", name='ck_worker_state'))
    op.create_index('ix_worker_heartbeats_last_seen', 'worker_heartbeats', ['last_seen'])


def downgrade():
    op.drop_table('worker_heartbeats')
