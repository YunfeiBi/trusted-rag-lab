"""Team approval boundary and conservative one-team-per-existing-project backfill."""
import uuid
from alembic import op
import sqlalchemy as sa

revision='0007_teams'
down_revision='0006_accounts'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('app_users',sa.Column('academic_identity',sa.String(16),nullable=False,server_default='unspecified'))
    op.add_column('app_users',sa.Column('teacher_verified',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_table('teams',sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('name',sa.String(100),nullable=False),sa.Column('description',sa.Text(),nullable=False),sa.Column('code',sa.String(16),nullable=False,unique=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('team_memberships',sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id'),primary_key=True),sa.Column('user_id',sa.Uuid(),sa.ForeignKey('app_users.id'),primary_key=True),sa.Column('role',sa.String(16),nullable=False),sa.Column('joined_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.CheckConstraint("role IN ('owner','admin','member')",name='ck_team_role'))
    op.create_table('team_applications',sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('team_id',sa.Uuid(),sa.ForeignKey('teams.id'),nullable=False),sa.Column('user_id',sa.Uuid(),sa.ForeignKey('app_users.id'),nullable=False),sa.Column('message',sa.Text(),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('review_note',sa.Text(),nullable=False),sa.Column('reviewed_by',sa.Uuid(),sa.ForeignKey('app_users.id')),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('reviewed_at',sa.DateTime(timezone=True)),sa.UniqueConstraint('team_id','user_id',name='uq_team_applicant'),sa.CheckConstraint("status IN ('pending','approved','rejected','cancelled')",name='ck_application_status'))
    op.create_index('ix_team_applications_team_id','team_applications',['team_id'])
    op.add_column('projects',sa.Column('team_id',sa.Uuid(),nullable=True))
    op.add_column('projects',sa.Column('is_team_library',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_foreign_key('fk_project_team','projects','teams',['team_id'],['id'])
    conn=op.get_bind()
    projects=conn.execute(sa.text('SELECT id,name FROM projects')).mappings().all()
    for project in projects:
        team_id=uuid.uuid4()
        # No union of unrelated project members or teacher identity assumptions.
        conn.execute(sa.text('INSERT INTO teams(id,name,description,code) VALUES (:id,:name,:description,:code)'),{'id':team_id,'name':(project['name']+'（迁移团队）')[:100],'description':'兼容迁移：仅承接原项目成员，身份待本人及管理员核实。','code':team_id.hex[:12].upper()})
        conn.execute(sa.text('UPDATE projects SET team_id=:team WHERE id=:project'),{'team':team_id,'project':project['id']})
        members=conn.execute(sa.text("SELECT user_id,role FROM memberships WHERE project_id=:id ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END,user_id"),{'id':project['id']}).mappings().all()
        for index,member in enumerate(members):
            role='owner' if index==0 else 'admin' if member['role']=='admin' else 'member'
            conn.execute(sa.text('INSERT INTO team_memberships(team_id,user_id,role) VALUES (:team,:user,:role)'),{'team':team_id,'user':member['user_id'],'role':role})
        library_id=uuid.uuid4()
        conn.execute(sa.text('INSERT INTO projects(id,name,description,team_id,is_team_library,created_at) VALUES (:id,:name,:description,:team,true,now())'),{'id':library_id,'name':'团队公共资料','description':'所有已批准团队成员可访问；原项目资料仍保持原项目授权。','team':team_id})
        for index,member in enumerate(members):
            conn.execute(sa.text('INSERT INTO memberships(project_id,user_id,role) VALUES (:project,:user,:role)'),{'project':library_id,'user':member['user_id'],'role':'admin' if index==0 or member['role']=='admin' else 'member'})
    op.alter_column('projects','team_id',nullable=False)
    op.create_index('ix_projects_team_id','projects',['team_id'])
    op.create_index('uq_team_library','projects',['team_id'],unique=True,postgresql_where=sa.text('is_team_library'))


def downgrade():
    # Public libraries may contain real data; downgrade never deletes them.
    op.drop_index('uq_team_library',table_name='projects')
    op.drop_index('ix_projects_team_id',table_name='projects')
    op.drop_constraint('fk_project_team','projects',type_='foreignkey')
    op.drop_column('projects','is_team_library')
    op.drop_column('projects','team_id')
    op.drop_table('team_applications')
    op.drop_table('team_memberships')
    op.drop_table('teams')
    op.drop_column('app_users','teacher_verified')
    op.drop_column('app_users','academic_identity')
