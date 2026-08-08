import json
import os
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from flask import Flask, render_template, request, jsonify
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "agenda.db"
CONFIG_PATH = BASE_DIR / "config.json"
app = Flask(__name__)
def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT,
            booking_date TEXT NOT NULL,
            booking_time TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
def generate_slots(config, event_type_id, target_date):
    """All grid slots (HH:MM) within THIS event type's business hours for a given date,
    excluding past times. Each event type has its own independent schedule."""
    event_type = config["event_types"][event_type_id]
    weekday = str(target_date.weekday())
    hours = event_type.get("business_hours", {}).get(weekday)
    if not hours:
        return []
    step = config["slot_duration_minutes"]
    start_h, start_m = map(int, hours["start"].split(":"))
    end_h, end_m = map(int, hours["end"].split(":"))
    start_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=start_h, minute=start_m)
    end_dt = datetime.combine(target_date, datetime.min.time()).replace(hour=end_h, minute=end_m)
    now = datetime.now()
    slots = []
    cur = start_dt
    while cur < end_dt:
        if cur > now:
            slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step)
    return slots
def get_slot_counts(conn, event_type_id, date_str):
    rows = conn.execute(
        "SELECT booking_time, COUNT(*) as c FROM bookings WHERE event_type=? AND booking_date=? GROUP BY booking_time",
        (event_type_id, date_str),
    ).fetchall()
    return {r["booking_time"]: r["c"] for r in rows}
def send_booking_notification(config, event_type_id, name, phone, address, date_str, time_str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    notify_email = os.environ.get("NOTIFY_EMAIL", smtp_user)

    if not smtp_user or not smtp_password:
        return  # notificaciones no configuradas todavía, no hace nada

    label = config["event_types"][event_type_id]["label"]
    lines = [
        f"Nueva reserva: {label}",
        f"Fecha: {date_str}",
        f"Hora: {time_str}",
        f"Cliente: {name}",
        f"Teléfono: {phone}",
    ]
    if address:
        lines.append(f"Dirección: {address}")

    msg = MIMEText("\n".join(lines))
    msg["Subject"] = f"Nueva reserva - {label}"
    msg["From"] = smtp_user
    msg["To"] = notify_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [notify_email], msg.as_string())
    except Exception as e:
        print("No se pudo enviar la notificación:", e)
@app.route("/")
def index():
    config = load_config()
    return render_template("index.html", config=config)
@app.route("/api/slots")
def api_slots():
    config = load_config()
    event_type_id = request.args.get("event_type")
    date_str = request.args.get("date")
    if event_type_id not in config["event_types"]:
        return jsonify({"error": "Tipo de evento inválido"}), 400
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify({"error": "Fecha inválida"}), 400
    all_slots = generate_slots(config, event_type_id, target_date)
    conn = get_db()
    counts = get_slot_counts(conn, event_type_id, date_str)
    conn.close()
    capacity = config["event_types"][event_type_id]["capacity_per_slot"]
    available = [s for s in all_slots if counts.get(s, 0) < capacity]
    return jsonify({"slots": available})
@app.route("/api/book", methods=["POST"])
def api_book():
    config = load_config()
    data = request.get_json(force=True) or {}
    event_type_id = data.get("event_type")
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    date_str = data.get("date")
    time_str = data.get("time")
    if event_type_id not in config["event_types"]:
        return jsonify({"error": "Tipo de evento inválido"}), 400
    if not name or not phone or not date_str or not time_str:
        return jsonify({"error": "Faltan datos obligatorios"}), 400
    if config["event_types"][event_type_id]["requires_address"] and not address:
        return jsonify({"error": "La dirección es obligatoria para envío con motorizado"}), 400
    # Re-validate the slot is still within this event type's business hours and not full
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Fecha inválida"}), 400
    valid_slots = generate_slots(config, event_type_id, target_date)
    if time_str not in valid_slots:
        return jsonify({"error": "Ese horario no está disponible para este servicio"}), 400
    conn = get_db()
    counts = get_slot_counts(conn, event_type_id, date_str)
    capacity = config["event_types"][event_type_id]["capacity_per_slot"]
    if counts.get(time_str, 0) >= capacity:
        conn.close()
        return jsonify({"error": "Ese horario ya no está disponible, elige otro"}), 409
    conn.execute(
        """
        INSERT INTO bookings (event_type, customer_name, phone, address, booking_date, booking_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_type_id, name, phone, address, date_str, time_str, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    send_booking_notification(config, event_type_id, name, phone, address, date_str, time_str)

    return jsonify({"ok": True})
@app.route("/admin")
def admin():
    config = load_config()
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM bookings
        ORDER BY (booking_date < ?), booking_date, booking_time
        """,
        (today_str,),
    ).fetchall()
    conn.close()
    return render_template("admin.html", bookings=rows, config=config)
@app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})
init_db()  # ensures the table exists both for `python3 app.py` and for gunicorn in production
if __name__ == "__main__":
    app.run(debug=True, port=5000)


