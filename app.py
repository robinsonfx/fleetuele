"""FleetUele — application locale offline-first de suivi de flotte.

La base SQLite est créée automatiquement au premier lancement. L'application
n'utilise aucune ressource distante et peut fonctionner sans connexion Internet.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("FLEETUELE_DB", ROOT / "data" / "fleetuele.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("FLEETUELE_SECRET", "fleetuele-local-session")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

RATES = {"Véhicule léger / Moto": 10.0, "Poids lourd / Machine": 50.0}
METHODS = ["Cash", "M-Pesa", "Airtel Money", "Orange Money", "Virement", "Autre"]
MAINTENANCE_TYPES = ["Vidange", "Filtres", "Freins", "Pneus", "Réparation", "Inspection", "Autre"]


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def month_later(value: date) -> date:
    """Retourne la même date le mois suivant, en gérant les mois plus courts."""
    year, month = value.year + (value.month == 12), (value.month % 12) + 1
    import calendar
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def init_db() -> None:
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            client TEXT NOT NULL DEFAULT 'Client Démo',
            label TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL CHECK(unit IN ('KM','HEURES')),
            interval REAL NOT NULL,
            initial_meter REAL NOT NULL DEFAULT 0,
            current_meter REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
            monthly_rate REAL NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'En attente' CHECK(status IN ('Payé','En attente')),
            invoice_no TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
            paid_at TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            reference TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS fuel_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            filled_at TEXT NOT NULL,
            litres REAL NOT NULL,
            cost REAL NOT NULL,
            currency TEXT NOT NULL CHECK(currency IN ('USD','CDF')),
            fx_rate REAL NOT NULL DEFAULT 2800,
            cost_usd REAL NOT NULL,
            meter REAL NOT NULL,
            station TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS maintenance_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            done_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            description TEXT DEFAULT '',
            cost REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL CHECK(currency IN ('USD','CDF')),
            fx_rate REAL NOT NULL DEFAULT 2800,
            cost_usd REAL NOT NULL,
            meter REAL NOT NULL,
            vendor TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_fuel_date ON fuel_logs(filled_at);
        CREATE INDEX IF NOT EXISTS idx_maint_date ON maintenance_logs(done_at);
        CREATE INDEX IF NOT EXISTS idx_pay_date ON payments(paid_at);
        """)
        if db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0:
            today = date.today()
            demo = [
                ("MOTO-01", "Transports Kivu", "Moto TVS HLX 125", "Véhicule léger / Moto", "KM", 2500, 18400, 20700, True),
                ("PICK-01", "Transports Kivu", "Pick-up Toyota Hilux 4x4", "Véhicule léger / Moto", "KM", 5000, 76200, 80150, True),
                ("CAM-01", "Transports Kivu", "Camion poids lourd Actros", "Poids lourd / Machine", "KM", 10000, 156000, 166300, True),
                ("POMPE-02", "Mines du Centre", "Motopompe diesel 6 pouces", "Poids lourd / Machine", "HEURES", 250, 3280, 3515, False),
                ("GEN-01", "Mines du Centre", "Groupe électrogène 150 kVA", "Poids lourd / Machine", "HEURES", 300, 2100, 2240, True),
            ]
            for code, client, label, category, unit, interval, initial, current, active in demo:
                cur = db.execute("INSERT INTO assets(code,client,label,category,unit,interval,initial_meter,current_meter,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (code, client, label, category, unit, interval, initial, current, today.isoformat()))
                asset_id = cur.lastrowid
                start = today - timedelta(days=12) if active else today - timedelta(days=75)
                end = today + timedelta(days=18) if active else today - timedelta(days=15)
                status = "Payé" if active else "En attente"
                inv = f"MF-{today.year}-{asset_id:04d}"
                subid = db.execute("INSERT INTO subscriptions(asset_id,monthly_rate,start_date,end_date,status,invoice_no) VALUES(?,?,?,?,?,?)",
                    (asset_id, RATES[category], start.isoformat(), end.isoformat(), status, inv)).lastrowid
                if active:
                    db.execute("INSERT INTO payments(subscription_id,paid_at,amount,method,reference,notes) VALUES(?,?,?,?,?,?)",
                        (subid, (today - timedelta(days=10)).isoformat(), RATES[category], "M-Pesa", f"DEMO-{asset_id:03d}", "Règlement de démonstration"))
                # Deux historiques par engin afin d'illustrer consommation et carnet.
                db.execute("INSERT INTO fuel_logs(asset_id,filled_at,litres,cost,currency,fx_rate,cost_usd,meter,station,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id, (today-timedelta(days=24)).isoformat(), 48 if unit == "KM" else 35, 75 if unit == "KM" else 55, "USD", 2800, 75 if unit == "KM" else 55, max(initial, current-900 if unit == "KM" else current-100), "Station Démo", "Plein de démonstration"))
                db.execute("INSERT INTO fuel_logs(asset_id,filled_at,litres,cost,currency,fx_rate,cost_usd,meter,station,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id, (today-timedelta(days=3)).isoformat(), 52 if unit == "KM" else 40, 82 if unit == "KM" else 63, "USD", 2800, 82 if unit == "KM" else 63, current, "Station Démo", "Plein de démonstration"))
                fraction_used = 1.05 if code == "CAM-01" else (0.90 if code == "MOTO-01" else 0.50)
                last_oil = max(initial, current - interval * fraction_used)
                db.execute("INSERT INTO maintenance_logs(asset_id,done_at,kind,description,cost,currency,fx_rate,cost_usd,meter,vendor) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id, (today-timedelta(days=37)).isoformat(), "Vidange", "Vidange et contrôle général", 100 if category == "Poids lourd / Machine" else 38, "USD", 2800, 100 if category == "Poids lourd / Machine" else 38, last_oil, "Atelier Démo"))


def subscription_status(row: sqlite3.Row) -> str:
    if row["end_date"] < date.today().isoformat():
        return "Expiré"
    return row["status"]


def asset_rows() -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute("""SELECT a.*, s.id subscription_id,s.monthly_rate,s.start_date,s.end_date,s.status,s.invoice_no,
            (SELECT MAX(meter) FROM maintenance_logs m WHERE m.asset_id=a.id AND m.kind='Vidange') last_oil_meter
            FROM assets a LEFT JOIN subscriptions s ON s.asset_id=a.id ORDER BY a.client,a.code""").fetchall()
    result = []
    today = date.today()
    for row in rows:
        d = dict(row)
        last_oil = d["last_oil_meter"] if d["last_oil_meter"] is not None else d["initial_meter"]
        remaining = float(d["interval"]) - (float(d["current_meter"]) - float(last_oil))
        d["remaining"] = remaining
        d["maint_status"] = "Dépassée" if remaining <= 0 else ("Proche" if remaining <= float(d["interval"]) * .15 else "À jour")
        d["sub_display"] = subscription_status(row) if row["subscription_id"] else "Non configuré"
        try:
            d["days_left"] = (date.fromisoformat(row["end_date"]) - today).days if row["end_date"] else None
        except (TypeError, ValueError):
            d["days_left"] = None
        result.append(d)
    return result


@app.context_processor
def inject_globals():
    return {"today": date.today().isoformat(), "rates": RATES, "methods": METHODS, "maintenance_types": MAINTENANCE_TYPES}


@app.route("/")
def dashboard():
    assets = asset_rows()
    today = date.today()
    month = today.strftime("%Y-%m")
    with get_db() as db:
        paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE substr(paid_at,1,7)=?", (month,)).fetchone()[0]
        fuel = db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM fuel_logs WHERE substr(filled_at,1,7)=?", (month,)).fetchone()[0]
        maintenance = db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM maintenance_logs WHERE substr(done_at,1,7)=?", (month,)).fetchone()[0]
        due = db.execute("""SELECT a.code,a.label,s.end_date,s.id FROM assets a JOIN subscriptions s ON s.asset_id=a.id
            WHERE s.end_date>=? AND s.end_date<=? ORDER BY s.end_date""", (today.isoformat(),(today+timedelta(days=5)).isoformat())).fetchall()
        recent = db.execute("""SELECT 'Plein' type, f.filled_at event_date,a.code,a.label,f.litres qty FROM fuel_logs f JOIN assets a ON a.id=f.asset_id
            UNION ALL SELECT m.kind,m.done_at,a.code,a.label,m.description FROM maintenance_logs m JOIN assets a ON a.id=m.asset_id
            ORDER BY event_date DESC LIMIT 7""").fetchall()
    return render_template("dashboard.html", assets=assets, due=due, recent=recent, paid=paid, fuel=fuel, maintenance=maintenance,
        total_monthly=sum(float(x["monthly_rate"] or 0) for x in assets if x["sub_display"] != "Expiré"),
        active=sum(x["sub_display"] in ("Payé","En attente") for x in assets), expiring=len(due), month=month)


@app.route("/assets", methods=["GET", "POST"])
def assets_page():
    if request.method == "POST":
        f = request.form
        asset_id = f.get("id", "").strip()
        code, client, label = f.get("code", "").strip().upper(), f.get("client", "").strip(), f.get("label", "").strip()
        category, unit = f.get("category", ""), f.get("unit", "")
        try:
            interval, initial, current = float(f["interval"]), float(f.get("initial_meter", 0)), float(f.get("current_meter", f.get("initial_meter", 0)))
            if not code or not client or not label or category not in RATES or unit not in ("KM", "HEURES") or interval <= 0 or min(initial,current) < 0:
                raise ValueError
            start = f.get("start_date") or date.today().isoformat()
            end = f.get("end_date") or month_later(date.fromisoformat(start)).isoformat()
            if date.fromisoformat(end) < date.fromisoformat(start):
                raise ValueError
            with get_db() as db:
                if asset_id:
                    db.execute("UPDATE assets SET code=?,client=?,label=?,category=?,unit=?,interval=?,initial_meter=?,current_meter=? WHERE id=?",
                               (code,client,label,category,unit,interval,initial,current,asset_id))
                    db.execute("UPDATE subscriptions SET monthly_rate=?,start_date=?,end_date=? WHERE asset_id=?",
                               (RATES[category],start,end,asset_id))
                else:
                    cur = db.execute("INSERT INTO assets(code,client,label,category,unit,interval,initial_meter,current_meter,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                     (code,client,label,category,unit,interval,initial,current,date.today().isoformat()))
                    aid = cur.lastrowid
                    inv = f"MF-{date.today().year}-{aid:04d}"
                    db.execute("INSERT INTO subscriptions(asset_id,monthly_rate,start_date,end_date,status,invoice_no) VALUES(?,?,?,?,?,?)",
                               (aid,RATES[category],start,end,"En attente",inv))
            flash("Engin enregistré avec son abonnement au tarif de la catégorie.", "success")
            return redirect(url_for("assets_page"))
        except sqlite3.IntegrityError:
            flash("Ce code engin existe déjà. Choisissez un code unique.", "error")
        except (KeyError, ValueError, TypeError):
            flash("Vérifiez les champs, les dates et les compteurs saisis.", "error")
    edit_id = request.args.get("edit", type=int)
    with get_db() as db:
        edit_asset = db.execute("SELECT a.*,s.start_date,s.end_date FROM assets a LEFT JOIN subscriptions s ON s.asset_id=a.id WHERE a.id=?", (edit_id,)).fetchone() if edit_id else None
    return render_template("assets.html", assets=asset_rows(), edit_asset=edit_asset)


@app.post("/assets/<int:asset_id>/delete")
def delete_asset(asset_id: int):
    with get_db() as db:
        db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    flash("Engin supprimé ainsi que ses données associées.", "success")
    return redirect(url_for("assets_page"))


@app.route("/subscriptions", methods=["GET", "POST"])
def subscriptions_page():
    if request.method == "POST":
        sub_id = request.form.get("subscription_id", type=int)
        amount = request.form.get("amount", type=float)
        paid_at = request.form.get("paid_at") or date.today().isoformat()
        method, reference, notes = request.form.get("method", "Cash"), request.form.get("reference", "").strip(), request.form.get("notes", "").strip()
        try:
            if amount is None or amount <= 0 or method not in METHODS:
                raise ValueError
            with get_db() as db:
                sub = db.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
                if not sub:
                    abort(404)
                db.execute("INSERT INTO payments(subscription_id,paid_at,amount,method,reference,notes) VALUES(?,?,?,?,?,?)", (sub_id,paid_at,amount,method,reference,notes))
                total_paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE subscription_id=?", (sub_id,)).fetchone()[0]
                new_status = "Payé" if total_paid >= float(sub["monthly_rate"]) else "En attente"
                db.execute("UPDATE subscriptions SET status=? WHERE id=?", (new_status,sub_id))
            flash("Règlement enregistré. Le statut est payé lorsque le montant cumulé couvre l’abonnement mensuel.", "success")
            return redirect(url_for("subscriptions_page"))
        except (ValueError, TypeError):
            flash("Montant ou moyen de paiement invalide.", "error")
    with get_db() as db:
        subs = db.execute("""SELECT s.*,a.code,a.label,a.client,a.category,
            (SELECT COALESCE(SUM(p.amount),0) FROM payments p WHERE p.subscription_id=s.id) paid_total
            FROM subscriptions s JOIN assets a ON a.id=s.asset_id ORDER BY s.end_date,a.code""").fetchall()
        payments = db.execute("""SELECT p.*,s.invoice_no,a.code,a.client FROM payments p JOIN subscriptions s ON s.id=p.subscription_id
            JOIN assets a ON a.id=s.asset_id ORDER BY p.paid_at DESC,p.id DESC LIMIT 30""").fetchall()
    return render_template("subscriptions.html", subs=[dict(s, display_status=subscription_status(s), days_left=(date.fromisoformat(s["end_date"])-date.today()).days) for s in subs], payments=payments)


@app.post("/subscriptions/<int:sub_id>/renew")
def renew_subscription(sub_id: int):
    with get_db() as db:
        sub = db.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if not sub:
            abort(404)
        today = date.today()
        old_end = date.fromisoformat(sub["end_date"])
        start = max(today, old_end + timedelta(days=1)) if old_end >= today else today
        end = month_later(start)
        db.execute("UPDATE subscriptions SET start_date=?,end_date=?,status='En attente' WHERE id=?", (start.isoformat(),end.isoformat(),sub_id))
    flash("Période d’abonnement renouvelée pour un mois. Enregistrez le règlement lorsqu’il est reçu.", "success")
    return redirect(url_for("subscriptions_page"))


@app.route("/fuel", methods=["GET", "POST"])
def fuel_page():
    if request.method == "POST":
        f=request.form
        try:
            asset_id=int(f["asset_id"]); litres=float(f["litres"]); cost=float(f["cost"]); meter=float(f["meter"])
            currency=f["currency"]; fx=float(f.get("fx_rate",2800)); filled_at=f.get("filled_at") or date.today().isoformat()
            if litres<=0 or cost<0 or meter<0 or currency not in ("USD","CDF") or fx<=0: raise ValueError
            with get_db() as db:
                asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
                if not asset: raise ValueError
                cost_usd=cost if currency=="USD" else cost/fx
                db.execute("INSERT INTO fuel_logs(asset_id,filled_at,litres,cost,currency,fx_rate,cost_usd,meter,station,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id,filled_at,litres,cost,currency,fx,cost_usd,meter,f.get("station",""),f.get("notes","")))
                db.execute("UPDATE assets SET current_meter=MAX(current_meter,?) WHERE id=?",(meter,asset_id))
            flash("Plein enregistré.","success"); return redirect(url_for("fuel_page"))
        except (ValueError,KeyError,TypeError): flash("Vérifiez l’engin, les litres, le coût et le compteur.","error")
    with get_db() as db:
        assets=db.execute("SELECT id,code,label,unit,current_meter FROM assets ORDER BY code").fetchall()
        logs=db.execute("""SELECT f.*,a.code,a.label,a.unit FROM fuel_logs f JOIN assets a ON a.id=f.asset_id ORDER BY f.filled_at DESC,f.id DESC""").fetchall()
    enriched=[]
    for r in logs:
        prev=next((p for p in logs if p["asset_id"]==r["asset_id"] and (p["meter"] < r["meter"] or (p["meter"]==r["meter"] and p["id"]<r["id"]))),None)
        distance=(float(r["meter"])-float(prev["meter"])) if prev else None
        consumption=(float(r["litres"])/distance*100 if r["unit"]=="KM" and distance and distance>0 else (float(r["litres"])/distance if r["unit"]=="HEURES" and distance and distance>0 else None))
        enriched.append(dict(r, distance=distance, consumption=consumption))
    return render_template("fuel.html", assets=assets, logs=enriched)


@app.route("/maintenance", methods=["GET", "POST"])
def maintenance_page():
    if request.method=="POST":
        f=request.form
        try:
            asset_id=int(f["asset_id"]); meter=float(f["meter"]); cost=float(f.get("cost",0)); currency=f["currency"]; fx=float(f.get("fx_rate",2800)); kind=f["kind"]
            if meter<0 or cost<0 or currency not in ("USD","CDF") or fx<=0 or kind not in MAINTENANCE_TYPES: raise ValueError
            with get_db() as db:
                asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
                if not asset: raise ValueError
                usd=cost if currency=="USD" else cost/fx
                db.execute("INSERT INTO maintenance_logs(asset_id,done_at,kind,description,cost,currency,fx_rate,cost_usd,meter,vendor) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (asset_id,f.get("done_at") or date.today().isoformat(),kind,f.get("description",""),cost,currency,fx,usd,meter,f.get("vendor","")))
                db.execute("UPDATE assets SET current_meter=MAX(current_meter,?) WHERE id=?",(meter,asset_id))
            flash("Intervention enregistrée. Le compteur vidange est réinitialisé automatiquement si le type est « Vidange ».","success")
            return redirect(url_for("maintenance_page"))
        except (ValueError,KeyError,TypeError): flash("Vérifiez les champs de l’intervention.","error")
    with get_db() as db:
        assets=db.execute("SELECT id,code,label,unit,current_meter FROM assets ORDER BY code").fetchall()
        logs=db.execute("SELECT m.*,a.code,a.label,a.unit FROM maintenance_logs m JOIN assets a ON a.id=m.asset_id ORDER BY m.done_at DESC,m.id DESC",).fetchall()
    return render_template("maintenance.html",assets=assets,logs=logs)


@app.route("/reports")
def reports_page():
    with get_db() as db:
        months=db.execute("""SELECT month, SUM(revenue) revenue, SUM(fuel) fuel, SUM(maintenance) maintenance FROM (
            SELECT substr(paid_at,1,7) month, SUM(amount) revenue,0 fuel,0 maintenance FROM payments GROUP BY substr(paid_at,1,7)
            UNION ALL SELECT substr(filled_at,1,7),0,SUM(cost_usd),0 FROM fuel_logs GROUP BY substr(filled_at,1,7)
            UNION ALL SELECT substr(done_at,1,7),0,0,SUM(cost_usd) FROM maintenance_logs GROUP BY substr(done_at,1,7)
        ) GROUP BY month ORDER BY month DESC LIMIT 18""").fetchall()
        clients=db.execute("""SELECT a.client,COUNT(*) assets,SUM(s.monthly_rate) monthly FROM assets a JOIN subscriptions s ON s.asset_id=a.id GROUP BY a.client ORDER BY a.client""").fetchall()
    return render_template("reports.html",months=months,clients=clients)


def csv_response(filename: str, headers: list[str], rows: list[list[Any]]) -> Response:
    buf=io.StringIO(newline="")
    writer=csv.writer(buf,delimiter=";")
    writer.writerow(headers); writer.writerows(rows)
    body="\ufeff"+buf.getvalue()
    return Response(body, mimetype="text/csv; charset=utf-8", headers={"Content-Disposition":f"attachment; filename={filename}"})


@app.get("/export/<kind>.csv")
def export_csv(kind: str):
    with get_db() as db:
        if kind=="assets":
            rows=db.execute("SELECT a.code,a.client,a.label,a.category,a.unit,a.interval,a.initial_meter,a.current_meter,s.monthly_rate,s.start_date,s.end_date,s.status,s.invoice_no FROM assets a JOIN subscriptions s ON s.asset_id=a.id ORDER BY a.code").fetchall()
            return csv_response("fleetuele-engins.csv",["Code","Client","Engin","Catégorie","Unité","Fréquence vidange","Compteur initial","Compteur actuel","Tarif USD/mois","Début","Fin","Statut","Facture"],[list(r) for r in rows])
        if kind=="payments":
            rows=db.execute("SELECT p.paid_at,a.client,a.code,s.invoice_no,p.amount,p.method,p.reference,p.notes FROM payments p JOIN subscriptions s ON s.id=p.subscription_id JOIN assets a ON a.id=s.asset_id ORDER BY p.paid_at DESC").fetchall()
            return csv_response("fleetuele-reglements.csv",["Date","Client","Code engin","Facture","Montant USD","Moyen","Référence","Notes"],[list(r) for r in rows])
        if kind=="fuel":
            rows=db.execute("SELECT f.filled_at,a.code,f.litres,f.cost,f.currency,f.cost_usd,f.meter,a.unit,f.station FROM fuel_logs f JOIN assets a ON a.id=f.asset_id ORDER BY f.filled_at DESC").fetchall()
            return csv_response("fleetuele-carburant.csv",["Date","Code","Litres","Coût","Devise","Coût USD","Compteur","Unité","Station"],[list(r) for r in rows])
        if kind=="maintenance":
            rows=db.execute("SELECT m.done_at,a.code,m.kind,m.description,m.cost,m.currency,m.cost_usd,m.meter,a.unit,m.vendor FROM maintenance_logs m JOIN assets a ON a.id=m.asset_id ORDER BY m.done_at DESC").fetchall()
            return csv_response("fleetuele-maintenance.csv",["Date","Code","Type","Description","Coût","Devise","Coût USD","Compteur","Unité","Prestataire"],[list(r) for r in rows])
    abort(404)


@app.get("/invoice/<int:sub_id>.pdf")
def invoice_pdf(sub_id: int):
    with get_db() as db:
        sub=db.execute("SELECT s.*,a.code,a.label,a.client,a.category FROM subscriptions s JOIN assets a ON a.id=s.asset_id WHERE s.id=?",(sub_id,)).fetchone()
        if not sub: abort(404)
        payments=db.execute("SELECT * FROM payments WHERE subscription_id=? ORDER BY paid_at",(sub_id,)).fetchall()
    buffer=io.BytesIO(); doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=46,leftMargin=46,topMargin=48,bottomMargin=48)
    styles=getSampleStyleSheet(); story=[Paragraph("FLEETUELE",styles["Title"]),Paragraph("FACTURE D’ABONNEMENT / REÇU",styles["Heading2"]),Spacer(1,14)]
    story.append(Paragraph(f"<b>N° facture :</b> {sub['invoice_no']}<br/><b>Client :</b> {sub['client']}<br/><b>Engin :</b> {sub['code']} — {sub['label']}<br/><b>Catégorie :</b> {sub['category']}<br/><b>Période :</b> {sub['start_date']} au {sub['end_date']}",styles["BodyText"]))
    story.append(Spacer(1,18))
    data=[["Désignation","Qté","Prix mensuel","Total USD"],[f"Abonnement flotte — {sub['code']}","1",f"{sub['monthly_rate']:.2f} $",f"{sub['monthly_rate']:.2f} $"]]
    table=Table(data,colWidths=[270,45,90,95]); table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#153f35")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#d7e2dc")),("PADDING",(0,0),(-1,-1),8),("ALIGN",(1,1),(-1,-1),"RIGHT")])); story.append(table)
    paid_total=sum(float(p["amount"]) for p in payments)
    story += [Spacer(1,12),Paragraph(f"<b>Statut :</b> {subscription_status(sub)} &nbsp;&nbsp; <b>Règlements cumulés :</b> {paid_total:.2f} USD",styles["BodyText"]),Spacer(1,16)]
    if payments:
        story.append(Paragraph("Historique des règlements",styles["Heading3"]))
        ptable=Table([["Date","Moyen","Référence","Montant USD"]]+[[p["paid_at"],p["method"],p["reference"] or "—",f"{p['amount']:.2f}"] for p in payments],colWidths=[90,120,170,120])
        ptable.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8f0eb")),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#d7e2dc")),("PADDING",(0,0),(-1,-1),7)])); story.append(ptable)
    story += [Spacer(1,32),Paragraph("Document généré localement par FleetUele.",styles["Italic"])]
    doc.build(story); buffer.seek(0)
    return send_file(buffer,mimetype="application/pdf",as_attachment=True,download_name=f"{sub['invoice_no']}.pdf")


@app.get("/health")
def health():
    return {"status":"ok","database":str(DB_PATH.name),"offline_first":True}


init_db()
if __name__ == "__main__":
    app.run(host=os.environ.get("FLEETUELE_HOST","127.0.0.1"),port=int(os.environ.get("PORT","5000")),debug=False)
