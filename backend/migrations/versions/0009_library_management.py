"""Document lifecycle and private search history."""
from alembic import op
import sqlalchemy as sa

revision = '0009_library_management'
down_revision = '0008_team_deletion'
branch_labels = depends_on = None


def upgrade():
    op.add_column('documents', sa.Column('deleted_at', sa.DateTime(timezone=True)))
    op.create_table('search_history',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('app_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('query', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_search_history_scope', 'search_history', ['user_id', 'project_id', 'created_at'])


def downgrade():
    op.drop_table('search_history')
    op.drop_column('documents', 'deleted_at')
