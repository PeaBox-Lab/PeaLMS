import json
import uuid
from .db import connection

def record(event_type, actor, entity_type, entity_id, details=None, correlation_id=None, conn=None):
    cid = correlation_id or str(uuid.uuid4())
    payload = json.dumps(details or {}, sort_keys=True, default=str)
    sql = "INSERT INTO audit_events(event_type,actor,entity_type,entity_id,details_json,correlation_id) VALUES(?,?,?,?,?,?)"
    if conn is not None:
        conn.execute(sql, (event_type, actor, entity_type, str(entity_id), payload, cid))
    else:
        with connection() as db:
            db.execute(sql, (event_type, actor, entity_type, str(entity_id), payload, cid))
    return cid
