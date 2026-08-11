import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, render_template, request, jsonify

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

# Render's servers run in UTC, but the business hours in config.json are Peru local time.
# Without this, "now" gets compared in the wrong timezone and today's slots can look
# like they've already passed (or vanish entirely) once it's evening in Peru.
BUSINESS_TZ = ZoneInfo("America/Lima")


def business_now():
    """Current wall-clock time in Peru, as a naive datetime (matches the naive
    datetimes built from config.json's business_hours)."""
    return datetime.now(BUSINESS_TZ).replace(tzinfo=None)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    # psycopg2 wants the "postgresql://" scheme; some providers hand out "postgres://"
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "Falta la variable de entorno DATABASE_URL. Configúrala en Render con la "
            "cadena de conexión de tu base de datos Neon."
        )
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
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
    cur.close()
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

    now = business_now()
    slots = []
    cur = start_dt
    while cur < end_dt:
        if cur > now:
            slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step)
    return slots


def get_slot_counts(conn, event_type_id, date_str):
    cur = conn.cursor()
    cur.execute(
        "SELECT booking_time, COUNT(*) as c FROM bookings WHERE event_type=%s AND booking_date=%s GROUP BY booking_time",
        (event_type_id, date_str),
    )
    rows = cur.fetchall()
    cur.close()
    return {r["booking_time"]: r["c"] for r in rows}


def send_booking_notification(config, event_type_id, name, phone, address, date_str, time_str):
    """Sends the notification over HTTPS via Resend's API instead of raw SMTP,
    because Render's free tier blocks outbound SMTP ports (25/465/587)."""
    api_key = os.environ.get("RESEND_API_KEY")
    notify_email = os.environ.get("NOTIFY_EMAIL")

    if not api_key or not notify_email:
        return  # notificaciones no configuradas todavía

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

    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "Agenda <onboarding@resend.dev>",
                "to": [notify_email],
                "subject": f"Nueva reserva - {label}",
                "text": "\n".join(lines),
            },
            timeout=10,
        )
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

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings (event_type, customer_name, phone, address, booking_date, booking_time, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (event_type_id, name, phone, address, date_str, time_str, business_now().isoformat()),
    )
    conn.commit()
    cur.close()
    conn.close()

    send_booking_notification(config, event_type_id, name, phone, address, date_str, time_str)

    return jsonify({"ok": True})


@app.route("/admin")
def admin():
    config = load_config()
    today_str = business_now().strftime("%Y-%m-%d")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM bookings
        ORDER BY (booking_date < %s), booking_date, booking_time
        """,
        (today_str,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("admin.html", bookings=rows, config=config)


@app.route("/admin/cancel/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM bookings WHERE id=%s", (booking_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"ok": True})


init_db()  # ensures the table exists both for `python3 app.py` and for gunicorn in production

if __name__ == "__main__":
    app.run(debug=True, port=5000)
