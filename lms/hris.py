import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from .db import connection
from .audit import record
from .services import EMPLOYEE_FIELDS, apply_policies

REQUIRED = {'employee_id','user_name','first_name','last_name','employment_status'}
VALID_STATUS = {'active','future','leave','terminated','inactive'}
ALLOWED_ROLES = {'learner','manager','instructor','learning_admin','compliance_officer','auditor'}

def _clean(value):
    return str(value or '').strip()

def _parse(csv_text):
    if not csv_text or not csv_text.strip():
        raise ValueError('CSV content is empty')
    reader = csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')))
    if not reader.fieldnames:
        raise ValueError('CSV header is missing')
    original_headers = list(reader.fieldnames)
    normalized_headers = [_clean(x) for x in original_headers]
    if len(set(normalized_headers)) != len(normalized_headers):
        raise ValueError('CSV contains duplicate columns after header normalization')
    headers = set(normalized_headers)
    missing = sorted(REQUIRED - headers)
    if missing:
        raise ValueError('Missing required columns: ' + ', '.join(missing))
    mapped_rows = []
    for raw in reader:
        mapped_rows.append({normalized_headers[i]: raw.get(original_headers[i], '') for i in range(len(original_headers))})
    return mapped_rows, normalized_headers

def analyze(csv_text, mode='full', max_population_change_pct=25):
    raw_rows, headers = _parse(csv_text)
    errors, normalized, seen = [], [], set()
    for idx, raw in enumerate(raw_rows, start=2):
        data = {field: _clean(raw.get(field,'')) for field in EMPLOYEE_FIELDS}
        data['display_name'] = data['display_name'] or f"{data['first_name']} {data['last_name']}".strip()
        eid = data['employee_id']
        row_errors = []
        for req in REQUIRED:
            if not data.get(req): row_errors.append(f'{req} is required')
        if eid in seen: row_errors.append('duplicate employee_id in file')
        seen.add(eid)
        if data['employment_status'].lower() not in VALID_STATUS:
            row_errors.append('invalid employment_status')
        if data['work_email'] and '@' not in data['work_email']:
            row_errors.append('invalid work_email')
        if data['manager_employee_id'] == eid and eid:
            row_errors.append('employee cannot manage self')
        if row_errors:
            errors.append({'row':idx,'employee_id':eid,'errors':row_errors})
        else:
            data['employment_status'] = data['employment_status'].lower()
            normalized.append(data)
    with connection() as conn:
        current = {r['employee_id']: dict(r) for r in conn.execute('SELECT * FROM employees').fetchall()}
        current_active = sum(1 for r in current.values() if r['employment_status']=='active')
    creates, updates, unchanged = [], [], []
    incoming_ids = set()
    for item in normalized:
        incoming_ids.add(item['employee_id'])
        old = current.get(item['employee_id'])
        if not old:
            creates.append(item['employee_id'])
        else:
            changed = {k:{'from':old.get(k) or '', 'to':item.get(k) or ''} for k in EMPLOYEE_FIELDS if str(old.get(k) or '') != str(item.get(k) or '')}
            if changed: updates.append({'employee_id':item['employee_id'],'changes':changed})
            else: unchanged.append(item['employee_id'])
    suspends = []
    if mode == 'full':
        suspends = [eid for eid,r in current.items() if r['employment_status']=='active' and eid not in incoming_ids]
    incoming_active = sum(1 for r in normalized if r['employment_status']=='active')
    change_pct = abs(incoming_active-current_active)*100/current_active if current_active else 0
    blockers = []
    if errors: blockers.append(f'{len(errors)} invalid row(s)')
    if mode == 'full' and current_active and change_pct > max_population_change_pct:
        blockers.append(f'active population change {change_pct:.1f}% exceeds {max_population_change_pct}% threshold')
    checksum = hashlib.sha256(csv_text.encode('utf-8')).hexdigest()
    return {
        'checksum': checksum, 'mode': mode, 'headers': headers, 'row_count': len(raw_rows),
        'accepted_count': len(normalized), 'rejected_count': len(errors), 'errors': errors,
        'creates': creates, 'updates': updates, 'unchanged': unchanged, 'suspends': suspends,
        'current_active': current_active, 'incoming_active': incoming_active,
        'population_change_pct': round(change_pct,2), 'blockers': blockers,
        'can_apply': not blockers, 'normalized': normalized
    }

def _derived_roles(employee, all_rows):
    roles = {('learner','self')}
    eid = employee['employee_id']
    if any(r.get('manager_employee_id') == eid and r.get('employment_status') == 'active' for r in all_rows):
        roles.add(('manager', f'manager:{eid}'))
    job = (employee.get('job_code') or '').upper()
    if job.startswith('TRN') or job.startswith('INSTR'):
        roles.add(('instructor', employee.get('department') or 'global'))
    return roles

def apply(csv_text, filename='employee_extract.csv', mode='full', actor='integration_operator', max_population_change_pct=25):
    analysis = analyze(csv_text, mode, max_population_change_pct)
    if not analysis['can_apply']:
        raise ValueError('Import blocked: ' + '; '.join(analysis['blockers']))
    with connection() as conn:
        duplicate = conn.execute("SELECT id FROM import_jobs WHERE checksum=? AND status='applied'", (analysis['checksum'],)).fetchone()
        if duplicate:
            return {'status':'duplicate','job_id':duplicate['id'],'analysis':{k:v for k,v in analysis.items() if k!='normalized'}}
        cur = conn.execute("INSERT INTO import_jobs(filename,checksum,mode,status,row_count,accepted_count,rejected_count,create_count,update_count,suspend_count,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (filename,analysis['checksum'],mode,'applying',analysis['row_count'],analysis['accepted_count'],analysis['rejected_count'],len(analysis['creates']),len(analysis['updates']),len(analysis['suspends']),json.dumps({k:v for k,v in analysis.items() if k!='normalized'})))
        job_id = cur.lastrowid
        for emp in analysis['normalized']:
            existing = conn.execute("SELECT employee_id FROM employees WHERE employee_id=?", (emp['employee_id'],)).fetchone()
            if existing:
                sets = ','.join(f"{f}=?" for f in EMPLOYEE_FIELDS if f!='employee_id')
                values = [emp[f] for f in EMPLOYEE_FIELDS if f!='employee_id'] + [datetime.now(timezone.utc).isoformat(),emp['employee_id']]
                conn.execute(f"UPDATE employees SET {sets}, updated_at=? WHERE employee_id=?", values)
                event = 'EmployeeUpdated'
            else:
                cols = ','.join(EMPLOYEE_FIELDS); marks=','.join('?' for _ in EMPLOYEE_FIELDS)
                conn.execute(f"INSERT INTO employees({cols}) VALUES({marks})", [emp[f] for f in EMPLOYEE_FIELDS])
                event = 'EmployeeCreated'
            conn.execute("DELETE FROM employee_roles WHERE employee_id=? AND assigned_by='hris'", (emp['employee_id'],))
            for role, scope in _derived_roles(emp, analysis['normalized']):
                conn.execute("INSERT OR IGNORE INTO employee_roles(employee_id,role,scope,assigned_by) VALUES(?,?,?,'hris')", (emp['employee_id'],role,scope))
            record(event, actor, 'employee', emp['employee_id'], {'import_job_id':job_id}, conn=conn)
        for eid in analysis['suspends']:
            conn.execute("UPDATE employees SET employment_status='inactive', updated_at=? WHERE employee_id=?", (datetime.now(timezone.utc).isoformat(),eid))
            conn.execute("DELETE FROM employee_roles WHERE employee_id=? AND role!='learner'", (eid,))
            record('AccountSuspended', actor, 'employee', eid, {'import_job_id':job_id,'reason':'missing_from_full_extract'}, conn=conn)
        policy_assignments = apply_policies([x['employee_id'] for x in analysis['normalized']], actor=actor, conn=conn)
        summary = {k:v for k,v in analysis.items() if k!='normalized'}
        summary['policy_assignments_created'] = policy_assignments
        conn.execute("UPDATE import_jobs SET status='applied', summary_json=?, completed_at=? WHERE id=?", (json.dumps(summary),datetime.now(timezone.utc).isoformat(),job_id))
        record('HrisImportApplied', actor, 'import_job', job_id, summary, conn=conn)
    return {'status':'applied','job_id':job_id,'analysis':summary}
