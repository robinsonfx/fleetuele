import tempfile
import unittest
from pathlib import Path

import app as fleet


class FleetAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fleet.DB_PATH = Path(self.tmp.name) / "test.sqlite3"
        fleet.init_db()
        fleet.app.config.update(TESTING=True)
        self.client = fleet.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_demo_seed(self):
        for path in ["/", "/assets", "/subscriptions", "/fuel", "/maintenance", "/reports"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        with fleet.get_db() as db:
            assets = db.execute("SELECT category,COUNT(*) n FROM assets GROUP BY category").fetchall()
            self.assertEqual(sum(x["n"] for x in assets), 5)
            statuses = {fleet.subscription_status(s) for s in db.execute("SELECT * FROM subscriptions")}
            self.assertIn("Expiré", statuses)
            self.assertTrue(statuses & {"Payé", "En attente"})
            categories = {r["category"] for r in db.execute("SELECT category FROM assets")}
            self.assertIn("Véhicule léger / Moto", categories)
            self.assertIn("Poids lourd / Machine", categories)

    def test_create_asset_and_rate_assignment(self):
        today = fleet.date.today().isoformat()
        response = self.client.post("/assets", data={
            "code":"TEST-01", "client":"Client Test", "label":"Camion test",
            "category":"Poids lourd / Machine", "unit":"KM", "interval":"5000",
            "initial_meter":"100", "current_meter":"100", "start_date":today, "end_date":""
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with fleet.get_db() as db:
            sub = db.execute("SELECT s.monthly_rate FROM subscriptions s JOIN assets a ON a.id=s.asset_id WHERE a.code='TEST-01'").fetchone()
            self.assertEqual(sub["monthly_rate"], 50)

    def test_fuel_and_maintenance_forms(self):
        with fleet.get_db() as db:
            aid = db.execute("SELECT id FROM assets WHERE code='MOTO-01'").fetchone()[0]
            before = db.execute("SELECT COUNT(*) FROM fuel_logs WHERE asset_id=?", (aid,)).fetchone()[0]
        self.client.post("/fuel", data={"asset_id":str(aid),"filled_at":fleet.date.today().isoformat(),"litres":"5","meter":"21000","cost":"8","currency":"USD","fx_rate":"2800"})
        with fleet.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM fuel_logs WHERE asset_id=?", (aid,)).fetchone()[0], before+1)
        self.client.post("/maintenance", data={"asset_id":str(aid),"done_at":fleet.date.today().isoformat(),"kind":"Vidange","meter":"21000","cost":"20","currency":"USD","fx_rate":"2800"})
        with fleet.get_db() as db:
            row = db.execute("SELECT MAX(meter) FROM maintenance_logs WHERE asset_id=? AND kind='Vidange'", (aid,)).fetchone()[0]
            self.assertEqual(row, 21000)

    def test_partial_and_full_payment_status(self):
        with fleet.get_db() as db:
            sub = db.execute("SELECT s.id,s.monthly_rate FROM subscriptions s JOIN assets a ON a.id=s.asset_id WHERE a.code='POMPE-02'").fetchone()
            db.execute("DELETE FROM payments WHERE subscription_id=?", (sub["id"],))
            db.execute("UPDATE subscriptions SET status='En attente' WHERE id=?", (sub["id"],))
        amount = float(sub["monthly_rate"])
        self.client.post("/subscriptions", data={"subscription_id":sub["id"],"amount":str(amount/2),"paid_at":fleet.date.today().isoformat(),"method":"Cash"})
        with fleet.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM subscriptions WHERE id=?", (sub["id"],)).fetchone()[0], "En attente")
        self.client.post("/subscriptions", data={"subscription_id":sub["id"],"amount":str(amount/2),"paid_at":fleet.date.today().isoformat(),"method":"M-Pesa"})
        with fleet.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM subscriptions WHERE id=?", (sub["id"],)).fetchone()[0], "Payé")
        pdf = self.client.get(f"/invoice/{sub['id']}.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_csv_exports_and_report(self):
        for kind in ["assets", "fuel", "maintenance", "payments"]:
            response = self.client.get(f"/export/{kind}.csv")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/csv", response.content_type)
            self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.client.get("/reports").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
