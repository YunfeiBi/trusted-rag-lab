from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import TSVECTOR, JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'app_users'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginSession(Base):
    __tablename__ = 'app_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('app_users.id', ondelete='CASCADE'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = 'memberships'
    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name='ck_membership_role'),)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('app_users.id', ondelete='CASCADE'), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class StoredVector(Vector):
    cache_ok = True

    def get_col_spec(self, **kw):
        return f'public.VECTOR({self.dim})'


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    latest_version: Mapped[int] = mapped_column(Integer, default=0)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('document_versions.id', name='fk_current_document_version', use_alter=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentVersion(Base):
    __tablename__ = 'document_versions'
    __table_args__ = (
        UniqueConstraint('document_id', 'number', name='uq_document_version_number'),
        CheckConstraint("status IN ('queued','processing','ready','failed','cancelled')", name='ck_version_status'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    file_hash: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default='queued')
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey('app_users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestionTask(Base):
    __tablename__ = 'ingestion_tasks'
    __table_args__ = (
        CheckConstraint("status IN ('queued','processing','succeeded','failed','cancelled')", name='ck_task_status'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('document_versions.id', ondelete='CASCADE'), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    stage: Mapped[str] = mapped_column(String(30), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = 'worker_heartbeats'
    __table_args__ = (CheckConstraint("state IN ('idle','busy','stopped')", name='ck_worker_state'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Chunk(Base):
    __tablename__ = 'chunks'
    __table_args__ = (UniqueConstraint('version_id', 'ordinal', name='uq_chunk_ordinal'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('document_versions.id', ondelete='CASCADE'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(200), default='')
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    keyword_text: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('pg_catalog.simple'::regconfig, keyword_text)", persisted=True))
    embedding: Mapped[list[float]] = mapped_column(StoredVector(1024))


class QuestionRecord(Base):
    __tablename__ = 'question_records'
    __table_args__ = (CheckConstraint("status IN ('answered','insufficient_evidence','generation_unavailable','invalid_citations','evidence_changed')",name='ck_question_status'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('app_users.id'),index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id'),index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    claims: Mapped[list] = mapped_column(JSONB)
    citations: Mapped[list] = mapped_column(JSONB)
    evidence: Mapped[list] = mapped_column(JSONB)
    details: Mapped[dict] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
