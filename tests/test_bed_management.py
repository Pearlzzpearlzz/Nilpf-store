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
        self.current_property = "site:test-A"
        self.participants = [
            {"pid": 1, "name": "John D."},
            {"pid": 2, "name": "Tanya L."},
        ]

        self.app.config.update(
            TESTING=True,
            BED_DB_PATH=self.path,
            BED_PROPERTY_ID_PROVIDER=lambda: self.current_property,
            BED_PARTICIPANT_PROVIDER=lambda: self.participants,
        )

        register_bed_management(self.app)

        with self.app.app_context():
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

    def test_property_b_cannot_access_property_a_beds(self):
        self._seed(2)

        conn = sqlite3.connect(self.path)
        property_a_count = conn.execute(
            "SELECT COUNT(*) FROM bed_facilities WHERE property_id=?",
            ("site:test-A",),
        ).fetchone()[0]
        conn.close()

        self.assertEqual(property_a_count, 1)

        self.current_property = "site:test-B"

        response = self.client.get("/bed-management")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Main Shelter", response.data)

        blocked = self.client.post(
            "/bed-management/spaces/1/assign",
            data={"participant_id": "1"},
            follow_redirects=True,
        )

        self.assertIn(
            b"space was not found for this property",
            blocked.data,
        )

        conn = sqlite3.connect(self.path)
        assignments = conn.execute(
            "SELECT COUNT(*) FROM bed_assignments"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(assignments, 0)



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

        self.participants.extend(
            {"pid": pid, "name": f"Participant {pid}"}
            for pid in range(3, 143)
        )

        conn.executemany(
            """INSERT INTO bed_assignments(
                   space_id,
                   participant_id,
                   assigned_at,
                   property_id
               )
               VALUES (?,?,?,?)""",
            [
                (pid, str(pid), now, self.current_property)
                for pid in range(1,143)
            ],
        )
        conn.execute("UPDATE bed_spaces SET status='Occupied' WHERE id<=142")
        conn.commit(); conn.close()
        response=self.client.get("/bed-management?facility_id=1")
        self.assertIn(b"Occupied</span><strong>142",response.data)
        self.assertIn(b"76.3%",response.data)


if __name__ == "__main__":
    unittest.main()
