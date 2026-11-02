"""Private question history and evidence snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0003_questions'
down_revision = '0002_documents'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('question_records',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('app_users.id'),nullable=False),
        sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('question',sa.Text(),nullable=False),
        sa.Column('answer',sa.Text(),nullable=False),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('claims',JSONB,nullable=False),
        sa.Column('citations',JSONB,nullable=False),
        sa.Column('evidence',JSONB,nullable=False),
        sa.Column('details',JSONB,nullable=False),
        sa.Column('trace_id',sa.String(32),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("status IN ('answered','insufficient_evidence','generation_unavailable','invalid_citations','evidence_changed')",name='ck_question_status'))
    op.create_index('ix_question_records_user_id','question_records',['user_id'])
    op.create_index('ix_question_records_project_id','question_records',['project_id'])


def downgrade():
    op.drop_table('question_records')
