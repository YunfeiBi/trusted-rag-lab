"""Project audit events and retrieval evaluation cases/runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005_centers'
down_revision = '0004_worker_health'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audit_events',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('actor_id',sa.Uuid(),sa.ForeignKey('app_users.id'),nullable=False),
        sa.Column('action',sa.String(64),nullable=False),sa.Column('target_id',sa.String(64),nullable=False),
        sa.Column('details',JSONB,nullable=False),sa.Column('trace_id',sa.String(32),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('evaluation_cases',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('created_by',sa.Uuid(),sa.ForeignKey('app_users.id'),nullable=False),
        sa.Column('question',sa.Text(),nullable=False),sa.Column('answerable',sa.Boolean(),nullable=False),
        sa.Column('gold_chunk_ids',JSONB,nullable=False),sa.Column('notes',sa.Text(),nullable=False),
        sa.Column('human_confirmed',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('evaluation_runs',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('app_users.id'),nullable=False),
        sa.Column('case_id',sa.Uuid(),sa.ForeignKey('evaluation_cases.id'),nullable=False),
        sa.Column('snapshot',JSONB,nullable=False),sa.Column('results',JSONB,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    for table in ['audit_events','evaluation_cases','evaluation_runs']:
        op.create_index('ix_'+table+'_project_id',table,['project_id'])


def downgrade():
    for table in ['evaluation_runs','evaluation_cases','audit_events']:
        op.drop_table(table)
