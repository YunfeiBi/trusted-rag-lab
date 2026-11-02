"""Documents, versions, leased tasks and searchable chunks."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


class FrozenVector(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return 'public.VECTOR(1024)'

revision = '0002_documents'
down_revision = '0001_identity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('documents',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('latest_version', sa.Integer(), nullable=False),
        sa.Column('current_version_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_documents_project_id', 'documents', ['project_id'])
    op.create_table('document_versions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('document_id', sa.Uuid(), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(200), nullable=False),
        sa.Column('storage_key', sa.String(100), nullable=False, unique=True),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('chunk_count', sa.Integer(), nullable=False),
        sa.Column('embedding_model', sa.String(200)),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('app_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('document_id','number',name='uq_document_version_number'),
        sa.CheckConstraint("status IN ('queued','processing','ready','failed','cancelled')",name='ck_version_status'))
    op.create_index('ix_document_versions_document_id', 'document_versions', ['document_id'])
    op.create_foreign_key('fk_current_document_version', 'documents', 'document_versions', ['current_version_id'], ['id'])
    op.create_table('ingestion_tasks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('version_id', sa.Uuid(), sa.ForeignKey('document_versions.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('stage', sa.String(30), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('lease_token', sa.Uuid()),
        sa.Column('error_code', sa.String(50)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued','processing','succeeded','failed','cancelled')",name='ck_task_status'))
    op.create_index('ix_ingestion_tasks_status', 'ingestion_tasks', ['status'])
    op.create_table('chunks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('version_id', sa.Uuid(), sa.ForeignKey('document_versions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('page', sa.Integer()),
        sa.Column('section', sa.String(200), nullable=False),
        sa.Column('line_start', sa.Integer()),
        sa.Column('line_end', sa.Integer()),
        sa.Column('keyword_text', sa.Text(), nullable=False),
        sa.Column('search_vector', TSVECTOR, sa.Computed("to_tsvector('pg_catalog.simple'::regconfig, keyword_text)",persisted=True), nullable=False),
        sa.Column('embedding', FrozenVector(), nullable=False),
        sa.UniqueConstraint('version_id','ordinal',name='uq_chunk_ordinal'))
    op.create_index('ix_chunks_version_id','chunks',['version_id'])
    op.create_index('ix_chunks_search','chunks',['search_vector'],postgresql_using='gin')


def downgrade():
    op.drop_table('chunks')
    op.drop_table('ingestion_tasks')
    op.drop_constraint('fk_current_document_version','documents',type_='foreignkey')
    op.drop_table('document_versions')
    op.drop_table('documents')
