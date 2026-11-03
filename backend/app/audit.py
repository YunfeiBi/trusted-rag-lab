"""Explicit metadata only: never accept arbitrary request bodies for auditing."""
from .models import AuditEvent

ALLOWED = {'role','version','decrypted','status','case_count'}


def record_event(db,project_id,actor_id,action,target_id,trace_id='',details=None):
    metadata = {k:v for k,v in (details or {}).items() if k in ALLOWED and type(v) in (str,int,bool)}
    db.add(AuditEvent(project_id=project_id,actor_id=actor_id,action=action,target_id=str(target_id),trace_id=trace_id,details=metadata))
