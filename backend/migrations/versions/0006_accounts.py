"""Optional QQ contact for existing accounts; required on new registration."""
from alembic import op
import sqlalchemy as sa

revision='0006_accounts'
down_revision='0005_centers'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('app_users',sa.Column('qq_email',sa.String(80),nullable=True))
    op.create_unique_constraint('uq_user_qq_email','app_users',['qq_email'])


def downgrade():
    op.drop_constraint('uq_user_qq_email','app_users',type_='unique')
    op.drop_column('app_users','qq_email')
