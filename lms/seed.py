from datetime import date, timedelta
from .db import connection
from .audit import record
from .services import apply_policies

def seed():
    with connection() as conn:
        if conn.execute('SELECT COUNT(*) FROM employees').fetchone()[0]:
            return False
        employees = [
          ('E1001','asha.sharma','Asha','Sharma','Asha Sharma','asha.sharma@example.com','active','2026-01-10','','','Nagarro','Technology','Engineering','Gurgaon','ENG-SSE','Senior Engineer','Engineering','G6','employee','India','Asia/Kolkata','en-IN','CC100','2026-11-15T08:00:00Z'),
          ('E1002','vikram.singh','Vikram','Singh','Vikram Singh','vikram.singh@example.com','active','2024-05-01','','','Nagarro','Technology','Engineering','Gurgaon','ENG-MGR','Engineering Manager','Engineering','G8','employee','India','Asia/Kolkata','en-IN','CC100','2026-11-15T08:00:00Z'),
          ('E1003','meera.iyer','Meera','Iyer','Meera Iyer','meera.iyer@example.com','active','2025-03-18','','E1002','Nagarro','Technology','Engineering','Bengaluru','ENG-SE','Engineer','Engineering','G5','employee','India','Asia/Kolkata','en-IN','CC101','2026-11-15T08:00:00Z'),
          ('E1004','daniel.kim','Daniel','Kim','Daniel Kim','daniel.kim@example.com','active','2023-07-12','','','Nagarro','People','Learning','Remote','TRN-001','Learning Instructor','Learning','G7','employee','India','Asia/Kolkata','en-IN','CC200','2026-11-15T08:00:00Z')
        ]
        conn.executemany("""INSERT INTO employees(employee_id,user_name,first_name,last_name,display_name,work_email,employment_status,hire_date,termination_date,manager_employee_id,legal_entity,business_unit,department,location,job_code,job_title,job_family,grade,worker_type,country,time_zone,locale,cost_center,source_last_modified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", employees)
        conn.executemany("INSERT INTO employee_roles(employee_id,role,scope,assigned_by) VALUES(?,?,?,'seed')", [
          ('E1001','learner','self'),('E1002','learner','self'),('E1002','manager','manager:E1002'),
          ('E1003','learner','self'),('E1004','learner','self'),('E1004','instructor','Learning'),('E1004','learning_admin','global')])
        courses = [
          ('SEC-101','Information Security Essentials','Security hygiene, acceptable use, phishing, and incident reporting.','Compliance',45,1,1,'1.0'),
          ('ETH-101','Code of Conduct','Corporate ethics, respectful workplace, and reporting obligations.','Compliance',35,1,1,'1.0'),
          ('ENG-201','Secure Engineering Foundations','Practical secure design and development practices.','Engineering',90,0,1,'1.0'),
          ('MGR-101','Manager Learning Dashboard','How managers monitor learning and support team development.','Leadership',30,0,1,'1.0')]
        conn.executemany("INSERT INTO courses(course_code,title,description,category,duration_minutes,mandatory,active,version) VALUES(?,?,?,?,?,?,?,?)", courses)
        conn.executemany("INSERT INTO assignment_policies(name,course_code,attribute,operator,value,due_days) VALUES(?,?,?,?,?,?)", [
          ('Security for all active employees','SEC-101','employment_status','equals','active',14),
          ('Code of Conduct for all active employees','ETH-101','employment_status','equals','active',30),
          ('Secure Engineering pathway','ENG-201','department','equals','Engineering',45)])
        record('SampleDataSeeded','system','platform','seed',{'employees':4,'courses':4},conn=conn)
    apply_policies(actor='system')
    return True
