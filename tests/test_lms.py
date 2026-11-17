import os
import unittest
from pathlib import Path

TEST_DB = Path(__file__).resolve().parents[1] / 'data' / 'test.db'
TEST_DB.unlink(missing_ok=True)
os.environ['LMS_DB_PATH'] = str(TEST_DB)

from lms.db import init_db, connection
from lms.seed import seed
from lms import services, hris
from lms.server import issue_demo_token, verify_token

VALID_CSV = """employee_id,user_name,first_name,last_name,display_name,work_email,employment_status,hire_date,termination_date,manager_employee_id,legal_entity,business_unit,department,location,job_code,job_title,job_family,grade,worker_type,country,time_zone,locale,cost_center,source_last_modified_at
E1001,asha.sharma,Asha,Sharma,Asha Sharma,asha.sharma@example.com,active,2026-01-10,,,,Nagarro,Technology,Engineering,Gurgaon,ENG-SSE,Senior Engineer,Engineering,G6,employee,India,Asia/Kolkata,en-IN,CC100,2026-11-16T00:00:00Z
E1002,vikram.singh,Vikram,Singh,Vikram Singh,vikram.singh@example.com,active,2024-05-01,,,,Nagarro,Technology,Engineering,Gurgaon,ENG-MGR,Engineering Manager,Engineering,G8,employee,India,Asia/Kolkata,en-IN,CC100,2026-11-16T00:00:00Z
E1003,meera.iyer,Meera,Iyer,Meera Iyer,meera.iyer@example.com,active,2025-03-18,,E1002,Nagarro,Technology,Engineering,Bengaluru,ENG-SE,Engineer,Engineering,G5,employee,India,Asia/Kolkata,en-IN,CC101,2026-11-16T00:00:00Z
E1004,daniel.kim,Daniel,Kim,Daniel Kim,daniel.kim@example.com,active,2023-07-12,,,,Nagarro,People,Learning,Remote,TRN-001,Learning Instructor,Learning,G7,employee,India,Asia/Kolkata,en-IN,CC200,2026-11-16T00:00:00Z
"""

class LmsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(); seed()

    @classmethod
    def tearDownClass(cls):
        TEST_DB.unlink(missing_ok=True)

    def test_seed_created_assignments(self):
        d = services.dashboard()
        self.assertEqual(d['active_employees'], 4)
        self.assertGreaterEqual(d['total_assignments'], 8)

    def test_dry_run_detects_updates(self):
        result = hris.analyze(VALID_CSV, mode='full')
        self.assertTrue(result['can_apply'])
        self.assertEqual(result['rejected_count'], 0)
        self.assertGreaterEqual(len(result['updates']), 1)

    def test_import_is_idempotent(self):
        first = hris.apply(VALID_CSV, filename='test.csv', mode='full')
        second = hris.apply(VALID_CSV, filename='test.csv', mode='full')
        self.assertEqual(first['status'], 'applied')
        self.assertEqual(second['status'], 'duplicate')

    def test_invalid_rows_block_import(self):
        bad = VALID_CSV.replace('E1001,asha.sharma', ',asha.sharma', 1)
        result = hris.analyze(bad, mode='full')
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['rejected_count'], 1)
        with self.assertRaises(ValueError): hris.apply(bad)

    def test_population_anomaly_blocks_full_extract(self):
        header = VALID_CSV.splitlines()[0]
        one = '\n'.join([header, VALID_CSV.splitlines()[1]]) + '\n'
        result = hris.analyze(one, mode='full', max_population_change_pct=10)
        self.assertFalse(result['can_apply'])
        self.assertTrue(any('population change' in x for x in result['blockers']))

    def test_manager_role_is_derived(self):
        employees = {e['employee_id']:e for e in services.list_employees()}
        roles = {r['role'] for r in employees['E1002']['roles']}
        self.assertIn('manager', roles)

    def test_completion_is_audited_and_not_overwritten(self):
        assignment = next(a for a in services.list_assignments('E1003') if a['status'] != 'completed')
        services.complete_assignment('E1003', assignment['course_code'], 92, 'test-user')
        with self.assertRaises(ValueError):
            services.complete_assignment('E1003', assignment['course_code'], 55, 'other-user')
        events = services.audit_events()
        matching = [e for e in events if e['event_type']=='LearningCompleted' and e['entity_id']==f"E1003:{assignment['course_code']}"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['actor'], 'test-user')

    def test_whitespace_headers_are_normalized(self):
        spaced = VALID_CSV.replace('employee_id,user_name', ' employee_id , user_name ', 1)
        result = hris.analyze(spaced, mode='delta')
        self.assertEqual(result['rejected_count'], 0)

    def test_personal_dashboard_is_scoped(self):
        d = services.personal_dashboard('E1003')
        self.assertEqual(d['active_employees'], 1)
        self.assertEqual(d['departments'], [])
        self.assertEqual(d['audit_events'], 0)

    def test_signed_demo_session(self):
        token = issue_demo_token('learner','E1003')
        identity = verify_token(token)
        self.assertEqual(identity['role'], 'learner')
        self.assertEqual(identity['actor'], 'E1003')
        self.assertIsNone(verify_token(token + 'tampered'))

if __name__ == '__main__': unittest.main()
