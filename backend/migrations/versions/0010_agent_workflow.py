"""Allow reviewed-agent terminal states; retain all existing question records."""
from alembic import op
import sqlalchemy as sa

revision = '0010_agent_workflow'
down_revision = '0009_library_management'
branch_labels = depends_on = None

LEGACY = "status IN ('answered','insufficient_evidence','generation_unavailable','invalid_citations','evidence_changed')"
CURRENT = "status IN ('answered','insufficient_evidence','generation_unavailable','invalid_citations','evidence_changed','review_rejected','workflow_stopped')"


def upgrade():
    op.drop_constraint('ck_question_status', 'question_records', type_='check')
    op.create_check_constraint('ck_question_status', 'question_records', CURRENT)


def downgrade():
    count = op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM question_records WHERE status IN ('review_rejected','workflow_stopped')"))
    if count:
        raise RuntimeError('Agent workflow records exist; export or explicitly migrate their statuses before downgrade.')
    op.drop_constraint('ck_question_status', 'question_records', type_='check')
    op.create_check_constraint('ck_question_status', 'question_records', LEGACY)
