import json
from datetime import date, datetime, timedelta, timezone
from .db import connection, rows, row
from .audit import record

EMPLOYEE_FIELDS = [
    'employee_id','user_name','first_name','last_name','display_name','work_email',
    'employment_status','hire_date','termination_date','manager_employee_id','legal_entity',
    'business_unit','department','location','job_code','job_title','job_family','grade',
    'worker_type','country','time_zone','locale','cost_center','source_last_modified_at'
]

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def list_employees():
    result = rows("SELECT * FROM employees ORDER BY display_name")
    roles = rows("SELECT employee_id, role, scope FROM employee_roles ORDER BY role")
    by_emp = {}
    for role in roles:
        by_emp.setdefault(role['employee_id'], []).append({'role': role['role'], 'scope': role['scope']})
    for item in result:
        item['roles'] = by_emp.get(item['employee_id'], [])
    return result

def create_employee(data, actor='admin'):
    required = ['employee_id','user_name','first_name','last_name']
    missing = [k for k in required if not str(data.get(k, '')).strip()]
    if missing:
        raise ValueError('Missing required fields: ' + ', '.join(missing))
    clean = {k: str(data.get(k, '') or '').strip() for k in EMPLOYEE_FIELDS}
    clean['display_name'] = clean['display_name'] or f"{clean['first_name']} {clean['last_name']}"
    clean['employment_status'] = clean['employment_status'] or 'active'
    with connection() as conn:
        cols = ','.join(EMPLOYEE_FIELDS)
        marks = ','.join('?' for _ in EMPLOYEE_FIELDS)
        conn.execute(f"INSERT INTO employees({cols}) VALUES({marks})", [clean[k] for k in EMPLOYEE_FIELDS])
        conn.execute("INSERT OR IGNORE INTO employee_roles(employee_id,role,scope,assigned_by) VALUES(?,?,?,?)", (clean['employee_id'],'learner','self',actor))
        record('EmployeeCreated', actor, 'employee', clean['employee_id'], clean, conn=conn)
    return get_employee(clean['employee_id'])

def get_employee(employee_id):
    emp = row("SELECT * FROM employees WHERE employee_id=?", (employee_id,))
    if emp:
        emp['roles'] = rows("SELECT role,scope FROM employee_roles WHERE employee_id=? ORDER BY role", (employee_id,))
    return emp

def list_courses():
    return rows("SELECT * FROM courses ORDER BY active DESC, title")

def create_course(data, actor='admin'):
    code = str(data.get('course_code','')).strip().upper()
    title = str(data.get('title','')).strip()
    if not code or not title:
        raise ValueError('course_code and title are required')
    values = (code, title, str(data.get('description','')), str(data.get('category','General')),
              int(data.get('duration_minutes',30)), 1 if data.get('mandatory') else 0,
              str(data.get('version','1.0')))
    with connection() as conn:
        conn.execute("INSERT INTO courses(course_code,title,description,category,duration_minutes,mandatory,version) VALUES(?,?,?,?,?,?,?)", values)
        record('CourseCreated', actor, 'course', code, dict(data), conn=conn)
    return row("SELECT * FROM courses WHERE course_code=?", (code,))

def create_policy(data, actor='admin'):
    required = ['name','course_code','attribute','value']
    missing = [x for x in required if not str(data.get(x,'')).strip()]
    if missing:
        raise ValueError('Missing required fields: '+', '.join(missing))
    with connection() as conn:
        cur = conn.execute("INSERT INTO assignment_policies(name,course_code,attribute,operator,value,due_days) VALUES(?,?,?,?,?,?)",
          (data['name'], data['course_code'], data['attribute'], data.get('operator','equals'), data['value'], int(data.get('due_days',30))))
        pid = cur.lastrowid
        record('AssignmentPolicyCreated', actor, 'assignment_policy', pid, data, conn=conn)
    return row("SELECT * FROM assignment_policies WHERE id=?", (pid,))

def list_policies():
    return rows("SELECT p.*, c.title course_title FROM assignment_policies p JOIN courses c ON c.course_code=p.course_code ORDER BY p.id DESC")

def policy_matches(emp, policy):
    actual = str(emp.get(policy['attribute'], '') or '').strip().lower()
    expected = str(policy['value']).strip().lower()
    op = policy.get('operator','equals')
    if op == 'equals': return actual == expected
    if op == 'contains': return expected in actual
    if op == 'in': return actual in [x.strip().lower() for x in expected.split('|')]
    return False

def apply_policies(employee_ids=None, actor='system', conn=None):
    owns = conn is None
    if owns:
        ctx = connection(); conn = ctx.__enter__()
    try:
        query = "SELECT * FROM employees WHERE employment_status='active'"
        params = []
        if employee_ids:
            query += " AND employee_id IN (%s)" % ','.join('?' for _ in employee_ids)
            params = list(employee_ids)
        employees = [dict(r) for r in conn.execute(query, params).fetchall()]
        policies = [dict(r) for r in conn.execute("SELECT * FROM assignment_policies WHERE active=1").fetchall()]
        created = 0
        for emp in employees:
            for pol in policies:
                if policy_matches(emp, pol):
                    due = (date.today() + timedelta(days=int(pol['due_days']))).isoformat()
                    cur = conn.execute("INSERT OR IGNORE INTO assignments(employee_id,course_code,due_date,assignment_source,policy_id) VALUES(?,?,?,?,?)",
                                       (emp['employee_id'], pol['course_code'], due, 'policy', pol['id']))
                    if cur.rowcount:
                        created += 1
                        record('AssignmentCreated', actor, 'assignment', f"{emp['employee_id']}:{pol['course_code']}", {'policy_id':pol['id'],'due_date':due}, conn=conn)
        if owns:
            ctx.__exit__(None,None,None)
        return created
    except Exception as exc:
        if owns: ctx.__exit__(type(exc),exc,exc.__traceback__)
        raise

def assign_course(employee_id, course_code, due_days=30, actor='admin'):
    due = (date.today() + timedelta(days=int(due_days))).isoformat()
    with connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO assignments(employee_id,course_code,due_date,assignment_source) VALUES(?,?,?,?)", (employee_id,course_code,due,'manual'))
        if not cur.rowcount:
            raise ValueError('Assignment already exists for this employee and course')
        record('AssignmentCreated', actor, 'assignment', f"{employee_id}:{course_code}", {'due_date':due,'source':'manual'}, conn=conn)
    return row("SELECT * FROM assignments WHERE employee_id=? AND course_code=?", (employee_id,course_code))

def complete_assignment(employee_id, course_code, score=None, actor=None):
    actor = actor or employee_id
    if score is not None:
        try: score = float(score)
        except (TypeError, ValueError): raise ValueError('Score must be numeric')
        if score < 0 or score > 100: raise ValueError('Score must be between 0 and 100')
    with connection() as conn:
        cur = conn.execute("UPDATE assignments SET status='completed', completed_at=?, score=? WHERE employee_id=? AND course_code=? AND status!='completed'",
                           (utc_now(), score, employee_id, course_code))
        if not cur.rowcount:
            existing = conn.execute("SELECT status FROM assignments WHERE employee_id=? AND course_code=?", (employee_id,course_code)).fetchone()
            if not existing: raise ValueError('Assignment not found')
            raise ValueError('Assignment is already completed')
        record('LearningCompleted', actor, 'assignment', f"{employee_id}:{course_code}", {'score':score}, conn=conn)
    return row("SELECT * FROM assignments WHERE employee_id=? AND course_code=?", (employee_id,course_code))

def list_assignments(employee_id=None):
    q = """SELECT a.*, e.display_name, e.department, c.title course_title, c.category, c.duration_minutes
           FROM assignments a JOIN employees e ON e.employee_id=a.employee_id
           JOIN courses c ON c.course_code=a.course_code"""
    params = ()
    if employee_id:
        q += " WHERE a.employee_id=?"; params=(employee_id,)
    q += " ORDER BY CASE a.status WHEN 'assigned' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, a.due_date"
    return rows(q, params)

def personal_dashboard(employee_id):
    with connection() as conn:
        counts = dict(conn.execute("""SELECT
          (SELECT COUNT(*) FROM courses WHERE active=1) active_courses,
          (SELECT COUNT(*) FROM assignments WHERE employee_id=?) total_assignments,
          (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status='completed') completed_assignments,
          (SELECT COUNT(*) FROM assignments WHERE employee_id=? AND status!='completed' AND due_date < date('now')) overdue_assignments
        """, (employee_id,employee_id,employee_id)).fetchone())
        counts['active_employees'] = 1
        counts['audit_events'] = 0
        counts['completion_rate'] = round(100*counts['completed_assignments']/counts['total_assignments'],1) if counts['total_assignments'] else 0
        counts['departments'] = []
        counts['assignment_status'] = [dict(r) for r in conn.execute("SELECT status label, COUNT(*) value FROM assignments WHERE employee_id=? GROUP BY status ORDER BY value DESC", (employee_id,)).fetchall()]
        return counts

def dashboard():
    with connection() as conn:
        counts = dict(conn.execute("""SELECT
          (SELECT COUNT(*) FROM employees WHERE employment_status='active') active_employees,
          (SELECT COUNT(*) FROM courses WHERE active=1) active_courses,
          (SELECT COUNT(*) FROM assignments) total_assignments,
          (SELECT COUNT(*) FROM assignments WHERE status='completed') completed_assignments,
          (SELECT COUNT(*) FROM assignments WHERE status!='completed' AND due_date < date('now')) overdue_assignments,
          (SELECT COUNT(*) FROM audit_events) audit_events
        """).fetchone())
        counts['completion_rate'] = round(100*counts['completed_assignments']/counts['total_assignments'],1) if counts['total_assignments'] else 0
        counts['departments'] = [dict(r) for r in conn.execute("SELECT COALESCE(department,'Unassigned') label, COUNT(*) value FROM employees WHERE employment_status='active' GROUP BY department ORDER BY value DESC").fetchall()]
        counts['assignment_status'] = [dict(r) for r in conn.execute("SELECT status label, COUNT(*) value FROM assignments GROUP BY status ORDER BY value DESC").fetchall()]
        return counts

def audit_events(limit=200):
    events = rows("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (int(limit),))
    for event in events:
        try: event['details'] = json.loads(event.pop('details_json'))
        except Exception: event['details'] = {}
    return events

def import_jobs():
    result = rows("SELECT * FROM import_jobs ORDER BY id DESC LIMIT 100")
    for item in result:
        try: item['summary'] = json.loads(item.pop('summary_json'))
        except Exception: item['summary'] = {}
    return result
