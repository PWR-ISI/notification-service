"""
Mock appointment-service + auth-identity-service for local Google Calendar
e2e testing. Two Flask apps on ports 8002 and 8001, started in threads.

Returns hardcoded data — minimal shape that api/services/event_enrichment.py
expects.

Replace PATIENT_EMAIL with the Gmail you OAuth-connected. The Google event
gets that address as an attendee, so the event lands on YOUR calendar.
"""
import threading

from flask import Flask, jsonify

# ── EDIT THIS BEFORE RUNNING ─────────────────────────────────────────────────
# Set PATIENT_EMAIL to the Gmail you OAuth-connected via /api/v2/google/auth-url
# (the address that appears in /api/v2/google/status response). The Google
# Calendar event uses this address as the patient attendee — without it the
# event won't land on a calendar you can see.
PATIENT_EMAIL = "REPLACE_ME@example.com"
PATIENT_ID    = 1                            # must match the event payload's patient_id
# ─────────────────────────────────────────────────────────────────────────────

DOCTOR_EMAIL = "doctor.test@example.com"     # we don't OAuth-connect doctor;
DOCTOR_ID    = 999                           # handler will skip its calendar


# ── appointment-service stub (port 8002) ─────────────────────────────────────
appointment_app = Flask("appointment_stub")

@appointment_app.route("/api/v2/appointments/<int:appointment_id>/", methods=["GET"])
def get_appointment(appointment_id):
    return jsonify({
        "id":           appointment_id,
        "patient_id":   PATIENT_ID,
        "doctor_id":    DOCTOR_ID,
        # 24h ahead — visible right at the top of the calendar UI
        "start_time":   "2026-05-27T10:00:00+02:00",
        "end_time":     "2026-05-27T10:30:00+02:00",
        "location":     "Klinika Centralna, Gabinet 3",
        "notes":        "Wizyta kontrolna",
    })


# ── auth-service stub (port 8001) ────────────────────────────────────────────
auth_app = Flask("auth_stub")

USERS = {
    PATIENT_ID: {"id": PATIENT_ID, "email": PATIENT_EMAIL, "first_name": "Anna",  "last_name": "Nowak"},
    DOCTOR_ID:  {"id": DOCTOR_ID,  "email": DOCTOR_EMAIL,  "first_name": "Jan",   "last_name": "Kowalski"},
}

@auth_app.route("/api/v2/users/<int:user_id>/", methods=["GET"])
def get_user(user_id):
    user = USERS.get(user_id)
    if not user:
        return jsonify({"detail": "Not found"}), 404
    return jsonify(user)


# ── Threaded launcher ────────────────────────────────────────────────────────
def run(app, port):
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    print(f"appointment-stub on http://localhost:8002")
    print(f"auth-stub        on http://localhost:8001")
    print(f"patient_id={PATIENT_ID} → email={PATIENT_EMAIL}")

    t1 = threading.Thread(target=run, args=(appointment_app, 8002), daemon=True)
    t2 = threading.Thread(target=run, args=(auth_app,        8001), daemon=True)
    t1.start()
    t2.start()
    # Block forever
    t1.join()
    t2.join()
