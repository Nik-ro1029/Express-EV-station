import math
import os
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "ev_station")
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")

if bool(MONGO_USER) != bool(MONGO_PASSWORD):
    raise ValueError("MONGO_USER and MONGO_PASSWORD must be set together")

CONNECTOR_TYPES = ["CCS2", "CHAdeMO", "Type 2", "GB/T"]
CHARGER_STATUSES = ["available", "occupied", "offline"]

app = Flask(__name__)
mongo_options = {"serverSelectionTimeoutMS": 5000}
if MONGO_USER and MONGO_PASSWORD:
    mongo_options.update(username=MONGO_USER, password=MONGO_PASSWORD, authSource="admin")
client = MongoClient(MONGO_URI, **mongo_options)
db = client[MONGO_DB]
stations = db.stations
sessions = db.sessions


# ---------- helpers ----------
def now():
    return datetime.now(timezone.utc)


def serialize(value):
    """Make Mongo documents JSON friendly (ObjectId -> str, datetime -> ISO)."""
    if isinstance(value, list):
        return [serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value


def oid(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def error(message, code=400):
    return jsonify({"error": message}), code


def make_charger(index, data):
    connector = str(data.get("connector_type", "")).strip()
    if connector not in CONNECTOR_TYPES:
        raise ValueError(f"connector_type must be one of {', '.join(CONNECTOR_TYPES)}")
    try:
        power = float(data.get("power_kw"))
    except (TypeError, ValueError):
        raise ValueError("power_kw must be a number")
    if not math.isfinite(power) or power <= 0:
        raise ValueError("power_kw must be a number greater than 0")
    return {
        "charger_id": f"C{index}",
        "connector_type": connector,
        "power_kw": power,
        "status": "available",
    }


def seed():
    """Insert a few sample stations on first run (safe to run many times)."""
    samples = [
        ("Central Plaza Hub", "Downtown", "12 Market Street", 0.32,
         [("CCS2", 60), ("CCS2", 60), ("Type 2", 22)]),
        ("Lakeside Mall Chargers", "Lakeside", "Level P1, Lakeside Mall", 0.28,
         [("Type 2", 22), ("Type 2", 22), ("CHAdeMO", 50)]),
        ("Airport Road Fast Stop", "North Gate", "Airport Road, Km 4", 0.40,
         [("CCS2", 120), ("CCS2", 120), ("GB/T", 60), ("CHAdeMO", 50)]),
    ]
    for name, city, address, price, chargers in samples:
        stations.update_one(
            {"name": name},
            {"$setOnInsert": {
                "name": name, "city": city, "address": address,
                "price_per_kwh": price, "created_at": now(),
                "chargers": [make_charger(i, {"connector_type": c, "power_kw": p})
                             for i, (c, p) in enumerate(chargers, start=1)],
            }},
            upsert=True,
        )


# ---------- health & stats ----------
@app.get("/api/health")
def health():
    try:
        client.admin.command("ping")
        return jsonify({"status": "ok", "database": "connected"})
    except Exception:
        return jsonify({"status": "degraded", "database": "unreachable"}), 503


@app.get("/api/stats")
def stats():
    all_stations = list(stations.find({}, {"chargers": 1}))
    chargers = [c for s in all_stations for c in s.get("chargers", [])]
    done = list(sessions.find({"status": "completed"}, {"energy_kwh": 1, "cost": 1}))
    return jsonify({
        "stations": len(all_stations),
        "chargers": len(chargers),
        "available": sum(c["status"] == "available" for c in chargers),
        "occupied": sum(c["status"] == "occupied" for c in chargers),
        "offline": sum(c["status"] == "offline" for c in chargers),
        "active_sessions": sessions.count_documents({"status": "active"}),
        "total_energy_kwh": round(sum(d.get("energy_kwh", 0) for d in done), 2),
        "total_revenue": round(sum(d.get("cost", 0) for d in done), 2),
    })


# ---------- stations ----------
@app.get("/api/stations")
def list_stations():
    return jsonify(serialize(list(stations.find().sort("name", 1))))


@app.get("/api/stations/<station_id>")
def get_station(station_id):
    _id = oid(station_id)
    doc = stations.find_one({"_id": _id}) if _id else None
    return jsonify(serialize(doc)) if doc else error("Station not found", 404)


@app.post("/api/stations")
def create_station():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return error("name is required")
    if stations.find_one({"name": name}):
        return error("A station with this name already exists", 409)
    try:
        price = float(data.get("price_per_kwh", 0))
        chargers = [make_charger(i, c) for i, c in enumerate(data.get("chargers", []), start=1)]
    except (TypeError, ValueError) as exc:
        return error(str(exc))
    if not math.isfinite(price) or price < 0:
        return error("price_per_kwh must be a number, 0 or more")
    doc = {
        "name": name,
        "city": str(data.get("city", "")).strip(),
        "address": str(data.get("address", "")).strip(),
        "price_per_kwh": price,
        "chargers": chargers,
        "created_at": now(),
    }
    try:
        doc["_id"] = stations.insert_one(doc).inserted_id
    except DuplicateKeyError:
        return error("A station with this name already exists", 409)
    return jsonify(serialize(doc)), 201


@app.post("/api/stations/<station_id>/chargers")
def add_charger(station_id):
    _id = oid(station_id)
    station = stations.find_one({"_id": _id}) if _id else None
    if not station:
        return error("Station not found", 404)
    try:
        charger = make_charger(len(station.get("chargers", [])) + 1, request.get_json(silent=True) or {})
    except ValueError as exc:
        return error(str(exc))
    stations.update_one({"_id": _id}, {"$push": {"chargers": charger}})
    return jsonify(charger), 201


@app.patch("/api/stations/<station_id>/chargers/<charger_id>")
def set_charger_status(station_id, charger_id):
    """Toggle a charger between available and offline (e.g. for maintenance)."""
    _id = oid(station_id)
    status = (request.get_json(silent=True) or {}).get("status")
    if not _id:
        return error("Station not found", 404)
    if status not in ("available", "offline"):
        return error("status must be 'available' or 'offline'")
    result = stations.update_one(
        {"_id": _id, "chargers": {"$elemMatch": {"charger_id": charger_id,
                                                 "status": {"$ne": "occupied"}}}},
        {"$set": {"chargers.$.status": status}},
    )
    if result.matched_count == 0:
        return error("Charger not found or currently in use", 409)
    return jsonify({"charger_id": charger_id, "status": status})


# ---------- charging sessions ----------
@app.get("/api/sessions")
def list_sessions():
    query = {}
    if request.args.get("status") in ("active", "completed"):
        query["status"] = request.args["status"]
    return jsonify(serialize(list(sessions.find(query).sort("started_at", -1).limit(100))))


@app.post("/api/sessions")
def start_session():
    data = request.get_json(silent=True) or {}
    station_id = oid(data.get("station_id"))
    charger_id = str(data.get("charger_id", "")).strip()
    vehicle = str(data.get("vehicle_number", "")).strip().upper()
    if not station_id or not charger_id or not vehicle:
        return error("station_id, charger_id and vehicle_number are required")

    # Atomically claim the charger so two drivers can't take the same one.
    station = stations.find_one_and_update(
        {"_id": station_id, "chargers": {"$elemMatch": {"charger_id": charger_id,
                                                        "status": "available"}}},
        {"$set": {"chargers.$.status": "occupied"}},
    )
    if not station:
        return error("Charger is not available", 409)

    session = {
        "station_id": station_id,
        "station_name": station["name"],
        "charger_id": charger_id,
        "vehicle_number": vehicle,
        "price_per_kwh": station["price_per_kwh"],
        "status": "active",
        "started_at": now(),
    }
    session["_id"] = sessions.insert_one(session).inserted_id
    return jsonify(serialize(session)), 201


@app.post("/api/sessions/<session_id>/stop")
def stop_session(session_id):
    _id = oid(session_id)
    session = sessions.find_one({"_id": _id, "status": "active"}) if _id else None
    if not session:
        return error("Active session not found", 404)
    try:
        energy = float((request.get_json(silent=True) or {}).get("energy_kwh"))
    except (TypeError, ValueError):
        return error("energy_kwh must be a number")
    if not math.isfinite(energy) or energy < 0:
        return error("energy_kwh must be a number, 0 or more")

    cost = round(energy * session["price_per_kwh"], 2)
    result = sessions.update_one(
        {"_id": _id, "status": "active"},
        {"$set": {"status": "completed", "ended_at": now(),
                  "energy_kwh": round(energy, 2), "cost": cost}},
    )
    if result.modified_count == 0:
        return error("Session was already stopped", 409)
    stations.update_one(
        {"_id": session["station_id"], "chargers.charger_id": session["charger_id"]},
        {"$set": {"chargers.$.status": "available"}},
    )
    return jsonify(serialize(sessions.find_one({"_id": _id})))


try:
    stations.create_index("name", unique=True)  # stops duplicate stations, even with several workers
    seed()
except Exception as exc:  # DB not ready yet; the API still starts
    app.logger.warning("Startup setup skipped: %s", exc)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG") == "1")
