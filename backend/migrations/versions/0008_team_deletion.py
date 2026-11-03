"""Logical team deletion keeps retained files and audit rows intact."""
from alembic import op
import sqlalchemy as sa

revision='0008_team_deletion'
down_revision='0007_teams'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('teams',sa.Column('is_active',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.add_column('teams',sa.Column('deleted_at',sa.DateTime(timezone=True),nullable=True))


def downgrade():
    # Do not silently resurrect logically deleted organizations on downgrade.
    conn=op.get_bind()
    if conn.execute(sa.text('SELECT count(*) FROM teams WHERE NOT is_active')).scalar_one():
        raise RuntimeError('Deleted teams exist; resolve retention policy before downgrading')
    op.drop_column('teams','deleted_at')
    op.drop_column('teams','is_active')
