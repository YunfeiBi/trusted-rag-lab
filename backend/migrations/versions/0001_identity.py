"""Accounts, revocable sessions and per-project memberships."""
from alembic import op
import sqlalchemy as sa

revision = '0001_identity'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('app_users',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('username', sa.String(64), nullable=False, unique=True),
        sa.Column('display_name', sa.String(100), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('app_sessions',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('app_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_app_sessions_user_id', 'app_sessions', ['user_id'])
    op.create_index('ix_app_sessions_expires_at', 'app_sessions', ['expires_at'])
    op.create_table('projects',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('memberships',
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('app_users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('role', sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'member')", name='ck_membership_role'))


def downgrade():
    op.drop_table('memberships')
    op.drop_table('projects')
    op.drop_table('app_sessions')
    op.drop_table('app_users')
