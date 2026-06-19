from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from functools import wraps
import os

# Load .env only in local dev (Vercel uses its own env var system)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
MONGO_URI   = os.environ.get("MONGO_URI", "")
DB_NAME     = os.environ.get("DB_NAME", "srms_db")
ADMIN_USER  = os.environ.get("ADMIN_USER", "SRMS")
ADMIN_PASS  = os.environ.get("ADMIN_PASS", "1234567")
app.secret_key = os.environ.get("SECRET_KEY", "srms_fallback_secret_2025")

# ── Lazy MongoDB connection (serverless-safe) ──────────────────────────────────
_mongo_client = None

def get_students():
    global _mongo_client
    if _mongo_client is None:
        if not MONGO_URI:
            raise Exception("MONGO_URI environment variable is not set on the server.")
        _mongo_client = MongoClient(
            MONGO_URI,
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=10000,
            maxPoolSize=1
        )
    return _mongo_client[DB_NAME]["students"]


def db_error_response(e):
    """Return a clean JSON error for any MongoDB failure."""
    global _mongo_client
    _mongo_client = None   # Reset so next request tries a fresh connection
    msg = str(e)
    if "MONGO_URI" in msg:
        hint = msg
    elif "SSL" in msg or "TLS" in msg:
        hint = "Database SSL error. Check MongoDB Atlas TLS settings."
    elif "timed out" in msg or "ServerSelectionTimeout" in msg:
        hint = "Cannot reach the database. Ensure MongoDB Atlas allows all IPs (0.0.0.0/0)."
    elif "Authentication" in msg or "auth" in msg.lower():
        hint = "Database authentication failed. Check MONGO_URI credentials."
    else:
        hint = f"Database error: {msg[:200]}"
    return jsonify({"success": False, "message": hint}), 503


# ── Auth Decorator ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ── Helper ─────────────────────────────────────────────────────────────────────
def serialize(doc):
    doc["_id"] = str(doc["_id"])
    return doc


# ── Auth Routes ────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET"])
def login_page():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_panel"))
    return render_template("login.html", error=None)


@app.route("/login", methods=["POST"])
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if username == ADMIN_USER and password == ADMIN_PASS:
        session["admin_logged_in"] = True
        session.permanent = False
        return redirect(url_for("admin_panel"))

    return render_template("login.html", error="Invalid username or password.")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Page Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def result_page():
    usn = request.args.get("usn", "").strip().upper()
    return render_template("index.html", usn=usn)


@app.route("/admin")
@login_required
def admin_panel():
    return render_template("admin.html")


# ── Diagnostic health check ────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    uri_set = bool(MONGO_URI)
    uri_preview = (MONGO_URI[:30] + "...") if uri_set else "NOT SET"
    try:
        students = get_students()
        count = students.count_documents({})
        return jsonify({
            "status": "ok",
            "mongo_uri_set": uri_set,
            "uri_preview": uri_preview,
            "db": DB_NAME,
            "student_count": count
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "mongo_uri_set": uri_set,
            "uri_preview": uri_preview,
            "db": DB_NAME,
            "error": str(e)[:500]
        }), 503


# ── API: Get All Records ───────────────────────────────────────────────────────
@app.route("/api/records", methods=["GET"])
@login_required
def get_records():
    try:
        students = get_students()
        all_docs = [serialize(doc) for doc in students.find()]
        return jsonify(all_docs)
    except Exception as e:
        return db_error_response(e)


# ── API: Upsert (Create or Update) Student ────────────────────────────────────
@app.route("/api/records", methods=["POST"])
@login_required
def save_record():
    try:
        students = get_students()
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
    except Exception as e:
        return db_error_response(e)


# ── API: Delete Student ────────────────────────────────────────────────────────
@app.route("/api/records/<usn>", methods=["DELETE"])
@login_required
def delete_record(usn):
    try:
        students = get_students()
        result = students.delete_one({"usn": usn.upper()})
        if result.deleted_count == 0:
            return jsonify({"success": False, "message": "Record not found."}), 404
        return jsonify({"success": True, "message": f"Record {usn.upper()} deleted."})
    except Exception as e:
        return db_error_response(e)


# ── API: Get Single Student Result (public) ───────────────────────────────────
@app.route("/api/result/<usn>", methods=["GET"])
def get_result(usn):
    try:
        students = get_students()
        doc = students.find_one({"usn": usn.strip().upper()})
        if not doc:
            return jsonify({"success": False, "message": "No record found for USN: " + usn.upper()}), 404

        marks      = doc.get("marks", {})
        total      = sum(int(v) for v in marks.values())
        max_marks  = len(marks) * 100
        percentage = round((total / max_marks) * 100, 1) if max_marks > 0 else 0
        status     = "PASS" if all(int(v) >= 35 for v in marks.values()) else "FAIL"

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
    except Exception as e:
        return db_error_response(e)


# ── Local dev only ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
