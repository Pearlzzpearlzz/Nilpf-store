import os
import sqlite3
import tempfile
import unittest

from flask import Flask

from bed_management import init_bed_management_db, register_bed_management


class BedManagementTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.app = Flask(__name__, template_folder="../templates", static_folder="../static")
        self.app.secret_key = "test"
        self.app.config.update(TESTING=True, BED_DB_PATH=self.path)
        register_bed_management(self.app)
        with self.app.app_context():
            conn = sqlite3.connect(self.path)
            conn.execute("CREATE TABLE participants(id INTEGER PRIMARY KEY, full_name TEXT)")
            conn.executemany("INSERT INTO participants(id,full_name) VALUES (?,?)", [(1,"John D."),(2,"Tanya L.")])
            conn.commit(); conn.close()
            init_bed_management_db()
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.path)

    def _seed(self, count=186):
        self.client.post("/bed-management/facilities", data={"name":"Main Shelter"})
        self.client.post("/bed-management/areas", data={"facility_id":1,"name":"Dorm A"})
        self.client.post("/bed-management/spaces/generate", data={"facility_id":1,"area_id":1,"count":count,"prefix":"Bed","start":1,"space_type":"Dorm Bed"})

    def test_generates_186_spaces_and_paginates(self):
        self._seed()
        response = self.client.get("/bed-management?facility_id=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Showing 1\xe2\x80\x9320 of 186 spaces", response.data)
        conn=sqlite3.connect(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bed_spaces").fetchone()[0],186)
        conn.close()

    def test_assignment_blocks_double_booking_and_preserves_history(self):
        self._seed(4)
        ok=self.client.post("/bed-management/spaces/1/assign",data={"facility_id":1,"participant_id":"1"},follow_redirects=True)
        self.assertIn(b"PID 1 assigned",ok.data)
        blocked=self.client.post("/bed-management/spaces/2/assign",data={"facility_id":1,"participant_id":"1"},follow_redirects=True)
        self.assertIn(b"already assigned",blocked.data)
        self.client.post("/bed-management/spaces/1/unassign",data={"facility_id":1,"reason":"Transfer"})
        conn=sqlite3.connect(self.path)
        assignment=conn.execute("SELECT unassigned_at,unassigned_reason FROM bed_assignments").fetchone()
        self.assertTrue(assignment[0]); self.assertEqual(assignment[1],"Transfer")
        conn.close()

    def test_out_of_service_counts_in_capacity_not_availability(self):
        self._seed(4)
        self.client.post("/bed-management/spaces/1/status",data={"facility_id":1,"new_status":"Out of Service"})
        response=self.client.get("/bed-management?facility_id=1")
        self.assertIn(b"Out of Service</span><strong>1",response.data)
        self.assertIn(b"Capacity</span><strong>4",response.data)
        self.assertIn(b"Available</span><strong>3",response.data)

    def test_186_bed_example_reports_76_point_3_percent(self):
        self._seed(186)
        conn=sqlite3.connect(self.path)
        now="2026-09-04T00:00:00+00:00"
        conn.executemany(
            "INSERT INTO participants(id,full_name) VALUES (?,?)",
            [(pid,f"Participant {pid}") for pid in range(3,143)],
        )
        conn.executemany(
            "INSERT INTO bed_assignments(space_id,participant_id,assigned_at) VALUES (?,?,?)",
            [(pid,str(pid),now) for pid in range(1,143)],
        )
        conn.execute("UPDATE bed_spaces SET status='Occupied' WHERE id<=142")
        conn.commit(); conn.close()
        response=self.client.get("/bed-management?facility_id=1")
        self.assertIn(b"Occupied</span><strong>142",response.data)
        self.assertIn(b"76.3%",response.data)


if __name__ == "__main__":
    unittest.main()
