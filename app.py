from flask import Flask, request, jsonify, render_template, abort
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId
import os

load_dotenv()

app = Flask(__name__)

# ── MongoDB Connection ─────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://achark659_db_user:1234567d@cluster0.ftbq3r1.mongodb.net/")
DB_NAME   = os.getenv("DB_NAME", "srms_db")

client = MongoClient(
    MONGO_URI,
    tlsAllowInvalidCertificates=True,   # Fix: TLS handshake error on Python 3.11 / Windows
    serverSelectionTimeoutMS=10000
)
db       = client[DB_NAME]
students = db["students"]

# Seed one default record if collection is empty
try:
    if students.count_documents({}) == 0:
        students.insert_one({
            "usn": "1SP25CS001",
            "name": "Karthik D",
            "marks": {
                "Mathematics": 88,
                "Data Structures": 92,
                "OOPs (Java)": 85,
                "Computer Arch.": 79,
                "Basic Electrical": 90
            }
        })
except Exception as e:
    print(f"[WARN] Could not seed database: {e}")

# ── Helper ─────────────────────────────────────────────────────────────────────
def serialize(doc):
    doc["_id"] = str(doc["_id"])
    return doc


# ── Page Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def result_page():
    usn = request.args.get("usn", "").strip().upper()
    return render_template("index.html", usn=usn)


@app.route("/admin")
def admin_panel():
    return render_template("admin.html")


# ── API: Get All Records ───────────────────────────────────────────────────────
@app.route("/api/records", methods=["GET"])
def get_records():
    all_docs = [serialize(doc) for doc in students.find()]
    return jsonify(all_docs)


# ── API: Upsert (Create or Update) Student ────────────────────────────────────
@app.route("/api/records", methods=["POST"])
def save_record():
    data  = request.get_json()
    usn   = data.get("usn", "").strip().upper()
    name  = data.get("name", "").strip()
    marks = data.get("marks", {})

    if not usn or not name:
        return jsonify({"success": False, "message": "USN and Name are required."}), 400

    students.update_one(
        {"usn": usn},
        {"$set": {"usn": usn, "name": name, "marks": marks}},
        upsert=True
    )
    return jsonify({"success": True, "message": f"Record for {usn} saved successfully!"})


# ── API: Delete Student ────────────────────────────────────────────────────────
@app.route("/api/records/<usn>", methods=["DELETE"])
def delete_record(usn):
    result = students.delete_one({"usn": usn.upper()})
    if result.deleted_count == 0:
        return jsonify({"success": False, "message": "Record not found."}), 404
    return jsonify({"success": True, "message": f"Record {usn.upper()} deleted."})


# ── API: Get Single Student Result ────────────────────────────────────────────
@app.route("/api/result/<usn>", methods=["GET"])
def get_result(usn):
    doc = students.find_one({"usn": usn.strip().upper()})
    if not doc:
        return jsonify({"success": False, "message": "Student not found."}), 404

    marks       = doc.get("marks", {})
    total       = sum(int(v) for v in marks.values())
    max_marks   = len(marks) * 100
    percentage  = round((total / max_marks) * 100, 1) if max_marks > 0 else 0
    status      = "PASS" if all(int(v) >= 35 for v in marks.values()) else "FAIL"

    # Build subject list with dummy codes
    subject_codes = ["22CSE11", "22CSE12", "22CSE13", "22CSE14", "22CSE15",
                     "22CSE16", "22CSE17", "22CSE18", "22CSE19", "22CSE20"]
    subjects_list = []
    for idx, (sub, mark) in enumerate(marks.items()):
        code = subject_codes[idx] if idx < len(subject_codes) else f"22CSE{21+idx}"
        subjects_list.append({"code": code, "name": sub, "max": 100, "obtained": int(mark)})

    return jsonify({
        "success":    True,
        "usn":        doc["usn"],
        "name":       doc["name"],
        "subjects":   subjects_list,
        "total":      total,
        "max_marks":  max_marks,
        "percentage": percentage,
        "status":     status
    })


if __name__ == '__main__':
    # use_reloader=False prevents WinError 10038 (socket error) on Windows
    # when Flask's debug reloader forks the process with live MongoDB connections
    app.run(debug=True, port=5000, use_reloader=False)
