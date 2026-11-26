"""Demo seed data.

Seeding is split so an existing pilot database can gain the content model
without duplicating employees or courses. Course metadata is only filled when
the field is still empty, so operator edits are never overwritten.
"""

import json

from .audit import record
from .db import connection
from .services import apply_policies


COURSE_META = {
    "SEC-101": {
        "summary": "Recognise phishing, protect credentials, and report incidents quickly.",
        "level": "Beginner",
        "author": "Security Office",
        "objectives": "Spot common social-engineering attacks\n"
                      "Apply safe credential and device habits\n"
                      "Report a suspected incident within minutes",
        "tags": "security,compliance,phishing,mandatory",
        "language": "en",
    },
    "ETH-101": {
        "summary": "How we make ethical decisions and speak up when something is wrong.",
        "level": "Beginner",
        "author": "People & Culture",
        "objectives": "Apply the code of conduct to everyday decisions\n"
                      "Recognise conflicts of interest\n"
                      "Use the right channel to raise a concern",
        "tags": "ethics,compliance,culture,mandatory",
        "language": "en",
    },
    "ENG-201": {
        "summary": "Practical secure design and coding habits for engineering teams.",
        "level": "Intermediate",
        "author": "Application Security",
        "objectives": "Model threats before writing code\n"
                      "Apply secure defaults and input validation\n"
                      "Use the OWASP Top 10 as a review checklist",
        "tags": "engineering,security,secure-coding",
        "language": "en",
    },
    "MGR-101": {
        "summary": "Run effective learning conversations and support your team's growth.",
        "level": "Intermediate",
        "author": "Learning & Development",
        "objectives": "Read team learning signals\n"
                      "Hold a development conversation\n"
                      "Escalate blockers early",
        "tags": "leadership,management,coaching",
        "language": "en",
    },
}


SECURITY_TEXT = (
    "Every employee is part of the security perimeter. A single careless click "
    "can expose customer data, interrupt operations and damage trust.\n\n"
    "In this module you will learn:\n"
    "- How attackers target people, not just systems.\n"
    "- The difference between a vulnerability and an incident.\n"
    "- What to do in the first five minutes of a suspected incident.\n\n"
    "Security is a habit, not a one-off training event. The goal is to make the "
    "safe choice the easy choice."
)

REPORT_TEXT = (
    "Report suspected incidents immediately. Do not try to investigate on your "
    "own and do not delete evidence.\n\n"
    "Use the security hotline or the incident portal. Include:\n"
    "- What happened, and when you noticed it.\n"
    "- Which systems, accounts or data were involved.\n"
    "- What you have already done (for example, disconnecting a device).\n\n"
    "Early reporting is always better than a perfect report submitted late. "
    "There is no penalty for reporting a false alarm."
)

ETHICS_TEXT = (
    "Our code of conduct is a decision aid, not a rulebook to memorise.\n\n"
    "When a situation feels ambiguous, ask yourself:\n"
    "- Is it legal and consistent with our policies?\n"
    "- Would I be comfortable if this decision were public?\n"
    "- Does it treat people with respect and fairness?\n\n"
    "If you are still unsure, raise it. Speaking up early protects you, your "
    "colleagues and the organization."
)

THREAT_TEXT = (
    "Threat modelling is a short, structured conversation about what can go "
    "wrong before code is written.\n\n"
    "A simple loop works well:\n"
    "1. What are we building, and what data does it touch?\n"
    "2. What can go wrong, and who would benefit?\n"
    "3. What are we doing about the highest risks?\n"
    "4. Did we verify that the controls actually work?\n\n"
    "Keep the output short enough that engineers will use it during design review."
)

COACHING_TEXT = (
    "Learning conversations work best when they are frequent, specific and "
    "forward-looking.\n\n"
    "A useful structure:\n"
    "- Start with what you have observed, using concrete examples.\n"
    "- Ask what support the person needs rather than prescribing a fix.\n"
    "- Agree one or two next actions with dates.\n"
    "- Follow up before the next review cycle.\n\n"
    "Use the team dashboard to spot overdue learning early, then treat it as a "
    "planning problem before it becomes a compliance problem."
)


def _module(conn, course_code, title, summary, position):
    cur = conn.execute(
        "INSERT INTO course_modules(course_code,title,summary,position) VALUES(?,?,?,?)",
        (course_code, title, summary, position),
    )
    return int(cur.lastrowid)


def _lesson(conn, module_id, course_code, title, lesson_type, content="",
            minutes=0, position=0, required=1, pass_score=70):
    cur = conn.execute(
        """
        INSERT INTO lessons(module_id,course_code,title,lesson_type,content,
                            duration_minutes,position,is_required,quiz_pass_score)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (module_id, course_code, title, lesson_type, content, minutes, position,
         1 if required else 0, pass_score),
    )
    return int(cur.lastrowid)


def _question(conn, lesson_id, prompt, options, correct, explanation, position):
    conn.execute(
        """
        INSERT INTO quiz_questions(lesson_id,prompt,options_json,correct_index,
                                   explanation,position)
        VALUES(?,?,?,?,?,?)
        """,
        (lesson_id, prompt, json.dumps(options), correct, explanation, position),
    )


def _seed_content(conn) -> bool:
    codes = {r["course_code"] for r in conn.execute("SELECT course_code FROM courses")}
    if not codes:
        return False
    seeded = {
        r["course_code"] for r in conn.execute(
            "SELECT DISTINCT course_code FROM course_modules"
        )
    }
    pending = [code for code in COURSE_META if code in codes and code not in seeded]
    if not pending:
        return False

    for code, meta in COURSE_META.items():
        if code not in codes:
            continue
        conn.execute(
            """
            UPDATE courses SET summary=?, level=?, author=?, objectives=?, tags=?,
                   language=?, status='published'
            WHERE course_code=? AND (summary IS NULL OR summary='')
            """,
            (meta["summary"], meta["level"], meta["author"], meta["objectives"],
             meta["tags"], meta["language"], code),
        )

    if "SEC-101" in pending:
        first = _module(conn, "SEC-101", "Security fundamentals",
                        "Why security is everyone's job.", 1)
        _lesson(conn, first, "SEC-101", "Why security matters", "text",
                SECURITY_TEXT, 6, 1)
        _lesson(conn, first, "SEC-101", "Phishing in three minutes", "youtube",
                "aqz-KE-bpKQ", 3, 2)
        second = _module(conn, "SEC-101", "Putting it into practice",
                         "Report incidents and check your knowledge.", 2)
        _lesson(conn, second, "SEC-101", "Reporting an incident", "text",
                REPORT_TEXT, 5, 1)
        quiz = _lesson(conn, second, "SEC-101", "Knowledge check", "quiz",
                       "Answer all questions. You need 70% to pass.", 5, 2, 1, 70)
        _question(conn, quiz, "What should you do first when you suspect a phishing email?",
                  ["Open the link to check", "Report it and do not click",
                   "Forward it to colleagues", "Delete it and move on"], 1,
                  "Report first. Opening or forwarding can expose others.", 1)
        _question(conn, quiz, "Which password practice is strongest?",
                  ["Reuse one long password", "Use a unique passphrase with MFA",
                   "Share it with your manager", "Store it on a sticky note"], 1,
                  "Unique credentials plus multi-factor authentication limit the blast radius.", 2)
        _question(conn, quiz, "A work laptop is lost. What is the priority?",
                  ["Wait until Monday", "Report it immediately",
                   "Buy a replacement first", "Change nothing"], 1,
                  "Immediate reporting lets the team revoke access and protect data.", 3)

    if "ETH-101" in pending:
        module = _module(conn, "ETH-101", "Our code of conduct",
                         "Ethical decisions and speaking up.", 1)
        _lesson(conn, module, "ETH-101", "Speaking up", "text", ETHICS_TEXT, 6, 1)
        quiz = _lesson(conn, module, "ETH-101", "Ethics check", "quiz",
                       "Two scenarios to test your judgement.", 4, 2, 1, 70)
        _question(conn, quiz, "A supplier offers you an expensive personal gift.",
                  ["Accept it quietly", "Decline and disclose it",
                   "Share it with the team", "Ask for cash instead"], 1,
                  "Decline and disclose. Transparency protects impartiality.", 1)
        _question(conn, quiz, "You witness harassing behaviour at a team event.",
                  ["Ignore it to avoid conflict", "Join in so you fit in",
                   "Support the person and report it", "Post about it publicly"], 2,
                  "Support the person and use the reporting channel.", 2)

    if "ENG-201" in pending:
        module = _module(conn, "ENG-201", "Secure by design",
                         "Threat modelling and secure coding habits.", 1)
        _lesson(conn, module, "ENG-201", "Threat modelling basics", "text",
                THREAT_TEXT, 8, 1)
        _lesson(conn, module, "ENG-201", "OWASP Top 10 reference", "link",
                "https://owasp.org/www-project-top-ten/", 4, 2)
        _lesson(conn, module, "ENG-201", "Secure coding habits", "youtube",
                "aqz-KE-bpKQ", 3, 3)

    if "MGR-101" in pending:
        module = _module(conn, "MGR-101", "Leading learning",
                         "Coaching conversations and team signals.", 1)
        _lesson(conn, module, "MGR-101", "Coaching conversations", "text",
                COACHING_TEXT, 7, 1)
        quiz = _lesson(conn, module, "MGR-101", "Manager check", "quiz",
                       "Check your approach to learning conversations.", 4, 2, 1, 70)
        _question(conn, quiz, "A team member is overdue on mandatory training. What is the best first step?",
                  ["Escalate to HR immediately", "Ask what is blocking them and agree a plan",
                   "Mark it complete for them", "Ignore it until review season"], 1,
                  "Diagnose the blocker first; escalate only if the plan fails.", 1)
        _question(conn, quiz, "What makes development feedback most useful?",
                  ["Vague praise", "Specific observations and agreed next actions",
                   "Comparing them to a colleague", "Waiting for the annual review"], 1,
                  "Specific, timely feedback with agreed actions drives change.", 2)

    return True


def _seed_engagement(conn) -> bool:
    """Seed the Moodle-like layer without overwriting operator-created data."""
    if conn.execute("SELECT COUNT(*) FROM cohorts").fetchone()[0]:
        return False
    employees = [r["employee_id"] for r in conn.execute("SELECT employee_id FROM employees")]
    courses = {r["course_code"] for r in conn.execute("SELECT course_code FROM courses")}
    if not employees or not courses:
        return False

    cohorts = [
        ("ALL-STAFF", "All staff", "Everyone in the demo organization.", employees),
        ("ENG-TEAM", "Engineering team",
         "Engineering employees for targeted technical learning.",
         [e for e in employees if e in {"E1001", "E1002", "E1003"}]),
    ]
    for code, name, description, members in cohorts:
        cur = conn.execute(
            "INSERT INTO cohorts(code,name,description) VALUES(?,?,?)",
            (code, name, description),
        )
        cohort_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO cohort_members(cohort_id,employee_id,added_by) VALUES(?,?,'seed')",
            [(cohort_id, employee_id) for employee_id in members],
        )

    paths = [
        ("CORE-ONBOARD", "Core onboarding", "Start with security, then code of conduct.",
         ["SEC-101", "ETH-101"]),
        ("SECURE-ENG", "Secure engineering", "Security foundations for engineers.",
         ["SEC-101", "ENG-201"]),
    ]
    path_ids = {}
    for code, title, description, codes in paths:
        available = [c for c in codes if c in courses]
        if not available:
            continue
        cur = conn.execute(
            "INSERT INTO learning_paths(code,title,description,status,created_by) "
            "VALUES(?,?,?,'published','system')",
            (code, title, description),
        )
        path_id = int(cur.lastrowid)
        path_ids[code] = path_id
        conn.executemany(
            "INSERT INTO learning_path_courses(path_id,course_code,position,is_required) "
            "VALUES(?,?,?,1)",
            [(path_id, course_code, position)
             for position, course_code in enumerate(available, start=1)],
        )

    badges = [
        ("SEC-FOUNDATION", "Security foundation", "Complete Information Security Essentials.",
         "shield", "SEC-101", None),
        ("ETH-FOUNDATION", "Ethics foundation", "Complete the Code of Conduct.",
         "book", "ETH-101", None),
        ("CORE-COMPLETE", "Core onboarding complete", "Finish the core onboarding path.",
         "certificate", None, path_ids.get("CORE-ONBOARD")),
    ]
    for code, title, description, icon, course_code, path_id in badges:
        if course_code and course_code not in courses:
            continue
        if path_id is None and course_code is None:
            continue
        conn.execute(
            "INSERT INTO badges(code,title,description,icon,criteria_course_code,criteria_path_id) "
            "VALUES(?,?,?,?,?,?)",
            (code, title, description, icon, course_code, path_id),
        )

    if "SEC-101" in courses:
        cur = conn.execute(
            "INSERT INTO discussions(course_code,title,body,created_by,pinned) "
            "VALUES('SEC-101','Questions about reporting incidents',"
            "'Ask here if you are unsure how to report a suspected incident. "
            "Do not include sensitive details in the thread.','E1004',1)"
        )
        discussion_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO discussion_posts(discussion_id,author_id,body) VALUES(?,?,?)",
            (discussion_id, "E1004",
             "Use the incident portal first. If access fails, call the security hotline "
             "and record the time you noticed the issue."),
        )

    record("SampleEngagementSeeded", "system", "platform", "seed",
           {"cohorts": len(cohorts), "paths": len(path_ids), "badges": len(badges)},
           conn=conn)
    return True


def seed():
    seeded = False
    with connection() as conn:
        if not conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
            employees = [
                ('E1001', 'asha.sharma', 'Asha', 'Sharma', 'Asha Sharma', 'asha.sharma@example.com', 'active', '2026-01-10', '', '', 'Nagarro', 'Technology', 'Engineering', 'Gurgaon', 'ENG-SSE', 'Senior Engineer', 'Engineering', 'G6', 'employee', 'India', 'Asia/Kolkata', 'en-IN', 'CC100', '2026-11-15T08:00:00Z'),
                ('E1002', 'vikram.singh', 'Vikram', 'Singh', 'Vikram Singh', 'vikram.singh@example.com', 'active', '2024-05-01', '', '', 'Nagarro', 'Technology', 'Engineering', 'Gurgaon', 'ENG-MGR', 'Engineering Manager', 'Engineering', 'G8', 'employee', 'India', 'Asia/Kolkata', 'en-IN', 'CC100', '2026-11-15T08:00:00Z'),
                ('E1003', 'meera.iyer', 'Meera', 'Iyer', 'Meera Iyer', 'meera.iyer@example.com', 'active', '2025-03-18', '', 'E1002', 'Nagarro', 'Technology', 'Engineering', 'Bengaluru', 'ENG-SE', 'Engineer', 'Engineering', 'G5', 'employee', 'India', 'Asia/Kolkata', 'en-IN', 'CC101', '2026-11-15T08:00:00Z'),
                ('E1004', 'daniel.kim', 'Daniel', 'Kim', 'Daniel Kim', 'daniel.kim@example.com', 'active', '2023-07-12', '', 'E1002', 'Nagarro', 'People', 'Learning', 'Remote', 'TRN-001', 'Learning Instructor', 'Learning', 'G7', 'employee', 'India', 'Asia/Kolkata', 'en-IN', 'CC200', '2026-11-15T08:00:00Z'),
            ]
            conn.executemany(
                "INSERT INTO employees(employee_id,user_name,first_name,last_name,display_name,work_email,employment_status,hire_date,termination_date,manager_employee_id,legal_entity,business_unit,department,location,job_code,job_title,job_family,grade,worker_type,country,time_zone,locale,cost_center,source_last_modified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                employees,
            )
            conn.executemany(
                "INSERT INTO employee_roles(employee_id,role,scope,assigned_by) VALUES(?,?,?,'seed')",
                [
                    ('E1001', 'learner', 'self'), ('E1002', 'learner', 'self'),
                    ('E1002', 'manager', 'manager:E1002'), ('E1003', 'learner', 'self'),
                    ('E1004', 'learner', 'self'), ('E1004', 'instructor', 'Learning'),
                    ('E1004', 'learning_admin', 'global'),
                ],
            )
            courses = [
                ('SEC-101', 'Information Security Essentials', 'Security hygiene, acceptable use, phishing, and incident reporting.', 'Compliance', 45, 1, 1, '1.0', 365, 80, ''),
                ('ETH-101', 'Code of Conduct', 'Corporate ethics, respectful workplace, and reporting obligations.', 'Compliance', 35, 1, 1, '1.0', 365, 80, ''),
                ('ENG-201', 'Secure Engineering Foundations', 'Practical secure design and development practices.', 'Engineering', 90, 0, 1, '1.0', 730, 70, ''),
                ('MGR-101', 'Manager Learning Dashboard', 'How managers monitor learning and support team development.', 'Leadership', 30, 0, 1, '1.0', 0, None, ''),
            ]
            conn.executemany(
                "INSERT INTO courses(course_code,title,description,category,duration_minutes,mandatory,active,version,recertification_days,passing_score,content_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                courses,
            )
            conn.executemany(
                "INSERT INTO assignment_policies(name,course_code,attribute,operator,value,due_days) VALUES(?,?,?,?,?,?)",
                [
                    ('Security for all active employees', 'SEC-101', 'employment_status', 'equals', 'active', 14),
                    ('Code of Conduct for all active employees', 'ETH-101', 'employment_status', 'equals', 'active', 30),
                    ('Secure Engineering pathway', 'ENG-201', 'department', 'equals', 'Engineering', 45),
                ],
            )
            seeded = True

        seeded_content = _seed_content(conn)
        seeded_engagement = _seed_engagement(conn)
        if seeded or seeded_content or seeded_engagement:
            record('SampleDataSeeded', 'system', 'platform', 'seed',
                   {'employees': 4, 'courses': 4, 'content': seeded_content,
                    'engagement': seeded_engagement}, conn=conn)

    apply_policies(actor='system')
    return seeded
