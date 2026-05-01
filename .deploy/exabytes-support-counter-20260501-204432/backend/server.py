from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SUPPORT_COUNTER_DB", ROOT / "agent_support_counter.db"))
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST_DIR", ROOT.parent / "dist"))
SESSION_HOURS = 10


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("document_checklist", "details"):
        if key in out and out[key]:
            try:
                out[key] = json.loads(out[key])
            except json.JSONDecodeError:
                pass
    return out


def rows_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_dict(row) or {} for row in rows]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL CHECK (role IN ('admin', 'supervisor')),
              password_hash TEXT NOT NULL,
              language TEXT NOT NULL DEFAULT 'en',
              status TEXT NOT NULL DEFAULT 'offline',
              created_at TEXT NOT NULL,
              last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES admin_users(id),
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consultations (
              id TEXT PRIMARY KEY,
              queue_number TEXT NOT NULL UNIQUE,
              source TEXT NOT NULL CHECK (source IN ('public', 'agent')),
              customer_name TEXT NOT NULL,
              customer_email TEXT,
              language TEXT NOT NULL CHECK (language IN ('en', 'ms')),
              topic TEXT NOT NULL,
              description TEXT NOT NULL,
              priority TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
              status TEXT NOT NULL CHECK (status IN ('waiting_human', 'assigned', 'active', 'needs_expert_review', 'resolved')),
              assigned_admin_id TEXT REFERENCES admin_users(id),
              needs_expert_review INTEGER NOT NULL DEFAULT 0,
              document_checklist TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              first_response_due_at TEXT NOT NULL,
              resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,
              consultation_id TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
              role TEXT NOT NULL CHECK (role IN ('customer', 'agent', 'ai', 'admin', 'system')),
              sender_name TEXT NOT NULL,
              content TEXT NOT NULL,
              language TEXT NOT NULL CHECK (language IN ('en', 'ms')),
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_events (
              id TEXT PRIMARY KEY,
              consultation_id TEXT NOT NULL REFERENCES consultations(id) ON DELETE CASCADE,
              classification TEXT NOT NULL,
              summary TEXT NOT NULL,
              confidence REAL NOT NULL,
              suggested_reply TEXT NOT NULL,
              escalation_reason TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
              id TEXT PRIMARY KEY,
              actor_type TEXT NOT NULL,
              actor_id TEXT,
              action TEXT NOT NULL,
              consultation_id TEXT,
              details TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        seed_users(conn)


def seed_users(conn: sqlite3.Connection) -> None:
    admin_password = os.getenv("SUPPORT_COUNTER_ADMIN_PASSWORD", "admin123")
    supervisor_password = os.getenv("SUPPORT_COUNTER_SUPERVISOR_PASSWORD", "super123")
    users = [
        ("adm-001", "Nurul Admin", "admin@counter.local", "admin", admin_password, "en"),
        ("adm-002", "Afiq Admin", "afiq@counter.local", "admin", admin_password, "ms"),
        ("sup-001", "Supervisor Lim", "supervisor@counter.local", "supervisor", supervisor_password, "en"),
    ]
    for user_id, name, email, role, password, language in users:
        exists = conn.execute("SELECT id FROM admin_users WHERE email = ?", (email,)).fetchone()
        if exists:
            if (
                (role == "admin" and os.getenv("SUPPORT_COUNTER_ADMIN_PASSWORD"))
                or (role == "supervisor" and os.getenv("SUPPORT_COUNTER_SUPERVISOR_PASSWORD"))
            ):
                conn.execute(
                    "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                    (hash_password(password), user_id),
                )
            continue
        conn.execute(
            """
            INSERT INTO admin_users (id, name, email, role, password_hash, language, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'offline', ?)
            """,
            (user_id, name, email, role, hash_password(password), language, now()),
        )


class LoginRequest(BaseModel):
    email: str
    password: str


class StatusRequest(BaseModel):
    status: Literal["online", "away", "offline"]


class ConsultationCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=120)
    customer_email: str | None = None
    language: Literal["en", "ms"] = "en"
    topic: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=8, max_length=4000)
    source: Literal["public", "agent"] = "public"


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    role: Literal["customer", "agent"] = "customer"
    language: Literal["en", "ms"] = "en"


class HandoffRequest(BaseModel):
    reason: str = Field(default="AI confidence below support threshold", max_length=500)


class ConsultationPatch(BaseModel):
    status: Literal["waiting_human", "assigned", "active", "needs_expert_review", "resolved"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    assigned_admin_id: str | None = None
    needs_expert_review: bool | None = None


def audit(
    conn: sqlite3.Connection,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    consultation_id: str | None,
    details: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (id, actor_type, actor_id, action, consultation_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), actor_type, actor_id, action, consultation_id, json.dumps(details), now()),
    )


def next_queue_number(conn: sqlite3.Connection) -> str:
    total = conn.execute("SELECT COUNT(*) AS n FROM consultations").fetchone()["n"]
    return f"SUP-{total + 1001:04d}"


def classify_case(topic: str, description: str, language: str) -> dict[str, Any]:
    text = f"{topic} {description}".lower()
    if any(k in text for k in ["refund", "payment", "invoice", "billing", "charge", "subscription", "receipt"]):
        classification = "Billing or payment support"
        priority = "medium"
        confidence = 0.86
        docs = ["Account email", "Order or invoice ID", "Payment receipt", "Screenshot of the issue"]
        escalation = None
    elif any(k in text for k in ["bug", "error", "login", "cannot", "failed", "crash", "technical", "api"]):
        classification = "Technical troubleshooting"
        priority = "medium"
        confidence = 0.82
        docs = ["Account email", "Screenshot or error message", "Device/browser details", "Steps to reproduce"]
        escalation = None
    elif any(k in text for k in ["complaint", "urgent", "angry", "cancel", "escalate", "manager", "refund now"]):
        classification = "Complaint or escalation"
        priority = "high"
        confidence = 0.72
        docs = ["Account email", "Previous ticket ID", "Conversation history", "Requested outcome"]
        escalation = "Sentiment or business impact requires human review."
    elif any(k in text for k in ["delivery", "shipment", "order", "booking", "appointment", "schedule"]):
        classification = "Order, booking, or service request"
        priority = "medium"
        confidence = 0.8
        docs = ["Order or booking ID", "Account email", "Preferred contact time", "Supporting screenshot"]
        escalation = None
    else:
        classification = "General customer enquiry"
        priority = "low"
        confidence = 0.66
        docs = ["Account email", "Short issue summary", "Relevant screenshot or file"]
        escalation = "General issue needs an admin to identify the correct workflow."

    if language == "ms":
        suggested = (
            "Terima kasih. Saya telah semak ringkasan awal dan senarai dokumen. "
            "Sila beri maklumat berkaitan; admin sokongan akan menyemak sebelum memberi jawapan akhir."
        )
    else:
        suggested = (
            "Thanks. I have prepared the initial summary and document checklist. "
            "Please upload the relevant context; a support admin will review before a final response is sent."
        )
    return {
        "classification": classification,
        "priority": priority,
        "confidence": confidence,
        "documents": docs,
        "escalation_reason": escalation,
        "suggested_reply": suggested,
    }


def get_active_load(conn: sqlite3.Connection, user_id: str) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) AS n FROM consultations
        WHERE assigned_admin_id = ? AND status IN ('assigned', 'active', 'needs_expert_review')
        """,
        (user_id,),
    ).fetchone()["n"]


def auto_assign_waiting(conn: sqlite3.Connection) -> None:
    waiting = conn.execute(
        """
        SELECT * FROM consultations
        WHERE status = 'waiting_human' AND assigned_admin_id IS NULL
        ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at ASC
        """
    ).fetchall()
    admins = conn.execute(
        """
        SELECT * FROM admin_users
        WHERE role = 'admin' AND status = 'online'
        ORDER BY last_seen DESC
        """
    ).fetchall()
    if not waiting or not admins:
        return

    for ticket in waiting:
        candidates = []
        for admin in admins:
            load = get_active_load(conn, admin["id"])
            language_penalty = 0 if admin["language"] == ticket["language"] else 1
            candidates.append((language_penalty, load, admin["id"]))
        _, _, assignee = sorted(candidates)[0]
        conn.execute(
            """
            UPDATE consultations
            SET status = 'assigned', assigned_admin_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (assignee, now(), ticket["id"]),
        )
        audit(
            conn,
            actor_type="system",
            actor_id=None,
            action="auto_assign",
            consultation_id=ticket["id"],
            details={"assigned_admin_id": assignee, "queue_number": ticket["queue_number"]},
        )


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    with connect() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
        if datetime.fromisoformat(session["expires_at"]) < datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
        user = conn.execute("SELECT * FROM admin_users WHERE id = ?", (session["user_id"],)).fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User missing")
        conn.execute(
            "UPDATE admin_users SET last_seen = ? WHERE id = ?",
            (now(), user["id"]),
        )
        return row_dict(user) or {}


def optional_user(authorization: str | None = Header(default=None)) -> dict[str, Any] | None:
    if not authorization:
        return None
    return current_user(authorization)


def require_supervisor(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "supervisor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Supervisor role required")
    return user


def cors_origins() -> list[str]:
    raw = os.getenv("SUPPORT_COUNTER_CORS_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://127.0.0.1:5173", "http://localhost:5173"]


app = FastAPI(title="WFH Agent Customer Support Counter", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now()}


@app.post("/v1/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    with connect() as conn:
        user = conn.execute("SELECT * FROM admin_users WHERE email = ?", (payload.email,)).fetchone()
        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user["id"], now(), expires.isoformat()),
        )
        conn.execute(
            "UPDATE admin_users SET status = 'online', last_seen = ? WHERE id = ?",
            (now(), user["id"]),
        )
        auto_assign_waiting(conn)
        clean = row_dict(user) or {}
        clean.pop("password_hash", None)
        clean["status"] = "online"
        return {"token": token, "expires_at": expires.isoformat(), "user": clean}


@app.get("/v1/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    user.pop("password_hash", None)
    return {"user": user}


@app.post("/v1/auth/logout", status_code=204)
def logout(response: Response, user: dict[str, Any] = Depends(current_user), authorization: str | None = Header(default=None)) -> Response:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.execute("UPDATE admin_users SET status = 'offline', last_seen = ? WHERE id = ?", (now(), user["id"]))
    response.status_code = 204
    return response


@app.patch("/v1/admin/me/status")
def set_status(payload: StatusRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            "UPDATE admin_users SET status = ?, last_seen = ? WHERE id = ?",
            (payload.status, now(), user["id"]),
        )
        auto_assign_waiting(conn)
        updated = conn.execute("SELECT * FROM admin_users WHERE id = ?", (user["id"],)).fetchone()
        out = row_dict(updated) or {}
        out.pop("password_hash", None)
        return {"user": out}


@app.post("/v1/consultations", status_code=201)
def create_consultation(payload: ConsultationCreate) -> dict[str, Any]:
    with connect() as conn:
        triage = classify_case(payload.topic, payload.description, payload.language)
        consultation_id = str(uuid.uuid4())
        queue_number = next_queue_number(conn)
        created = now()
        due = (datetime.now(timezone.utc) + timedelta(minutes=15 if triage["priority"] == "high" else 30)).isoformat()
        conn.execute(
            """
            INSERT INTO consultations (
              id, queue_number, source, customer_name, customer_email, language, topic, description,
              priority, status, document_checklist, created_at, updated_at, first_response_due_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting_human', ?, ?, ?, ?)
            """,
            (
                consultation_id,
                queue_number,
                payload.source,
                payload.customer_name,
                payload.customer_email,
                payload.language,
                payload.topic,
                payload.description,
                triage["priority"],
                json.dumps(triage["documents"]),
                created,
                created,
                due,
            ),
        )
        initial_role = "agent" if payload.source == "agent" else "customer"
        conn.execute(
            """
            INSERT INTO messages (id, consultation_id, role, sender_name, content, language, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), consultation_id, initial_role, payload.customer_name, payload.description, payload.language, created),
        )
        conn.execute(
            """
            INSERT INTO ai_events (id, consultation_id, classification, summary, confidence, suggested_reply, escalation_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                consultation_id,
                triage["classification"],
                f"{payload.customer_name} needs help with {payload.topic}. {payload.description[:220]}",
                triage["confidence"],
                triage["suggested_reply"],
                triage["escalation_reason"],
                created,
            ),
        )
        audit(
            conn,
            actor_type=payload.source,
            actor_id=None,
            action="create_consultation",
            consultation_id=consultation_id,
            details={"queue_number": queue_number, "classification": triage["classification"]},
        )
        auto_assign_waiting(conn)
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        ai_event = conn.execute("SELECT * FROM ai_events WHERE consultation_id = ?", (consultation_id,)).fetchone()
        return {"consultation": row_dict(consultation), "ai_event": row_dict(ai_event)}


@app.get("/v1/consultations/{consultation_id}")
def get_consultation(consultation_id: str) -> dict[str, Any]:
    with connect() as conn:
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not consultation:
            raise HTTPException(status_code=404, detail="Consultation not found")
        ahead = conn.execute(
            """
            SELECT COUNT(*) AS n FROM consultations
            WHERE status IN ('waiting_human', 'assigned', 'active', 'needs_expert_review')
              AND created_at < ?
            """,
            (consultation["created_at"],),
        ).fetchone()["n"]
        ai_event = conn.execute(
            "SELECT * FROM ai_events WHERE consultation_id = ? ORDER BY created_at DESC LIMIT 1",
            (consultation_id,),
        ).fetchone()
        out = row_dict(consultation) or {}
        out["queue_position"] = ahead + 1 if out["status"] != "resolved" else 0
        return {"consultation": out, "ai_event": row_dict(ai_event)}


@app.get("/v1/consultations/{consultation_id}/messages")
def get_messages(consultation_id: str) -> dict[str, Any]:
    with connect() as conn:
        exists = conn.execute("SELECT id FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Consultation not found")
        messages = conn.execute(
            "SELECT * FROM messages WHERE consultation_id = ? ORDER BY created_at ASC",
            (consultation_id,),
        ).fetchall()
        return {"messages": rows_dict(messages)}


@app.post("/v1/consultations/{consultation_id}/messages", status_code=201)
def post_message(
    consultation_id: str,
    payload: MessageCreate,
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    with connect() as conn:
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not consultation:
            raise HTTPException(status_code=404, detail="Consultation not found")
        role = payload.role
        sender_name = "Customer"
        actor_type = role
        actor_id = None
        if user:
            role = "admin"
            sender_name = user["name"]
            actor_type = user["role"]
            actor_id = user["id"]
            if user["role"] == "admin" and consultation["assigned_admin_id"] not in (None, user["id"]):
                raise HTTPException(status_code=403, detail="This case is assigned to another admin")
        message_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO messages (id, consultation_id, role, sender_name, content, language, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, consultation_id, role, sender_name, payload.content, payload.language, now()),
        )
        new_status = consultation["status"]
        if role == "admin" and consultation["status"] == "assigned":
            new_status = "active"
            conn.execute(
                "UPDATE consultations SET status = 'active', updated_at = ? WHERE id = ?",
                (now(), consultation_id),
            )
        else:
            conn.execute("UPDATE consultations SET updated_at = ? WHERE id = ?", (now(), consultation_id))
        audit(
            conn,
            actor_type=actor_type,
            actor_id=actor_id,
            action="post_message",
            consultation_id=consultation_id,
            details={"role": role, "status_after": new_status},
        )
        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return {"message": row_dict(message)}


@app.post("/v1/consultations/{consultation_id}/handoff")
def handoff(consultation_id: str, payload: HandoffRequest) -> dict[str, Any]:
    with connect() as conn:
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not consultation:
            raise HTTPException(status_code=404, detail="Consultation not found")
        conn.execute(
            """
            UPDATE consultations
            SET status = 'waiting_human', assigned_admin_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now(), consultation_id),
        )
        conn.execute(
            """
            INSERT INTO ai_events (id, consultation_id, classification, summary, confidence, suggested_reply, escalation_reason, created_at)
            VALUES (?, ?, 'AI handoff', ?, 0.42, 'A human support admin should review this conversation.', ?, ?)
            """,
            (str(uuid.uuid4()), consultation_id, f"AI requested handoff for {consultation['queue_number']}.", payload.reason, now()),
        )
        audit(
            conn,
            actor_type="ai",
            actor_id=None,
            action="handoff",
            consultation_id=consultation_id,
            details={"reason": payload.reason},
        )
        auto_assign_waiting(conn)
        updated = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        return {"consultation": row_dict(updated)}


@app.get("/v1/admin/queue")
def admin_queue(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as conn:
        auto_assign_waiting(conn)
        if user["role"] == "supervisor":
            consultations = conn.execute(
                "SELECT * FROM consultations ORDER BY CASE status WHEN 'resolved' THEN 1 ELSE 0 END, created_at DESC"
            ).fetchall()
        else:
            consultations = conn.execute(
                """
                SELECT * FROM consultations
                WHERE assigned_admin_id = ? OR status = 'waiting_human'
                ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC
                """,
                (user["id"],),
            ).fetchall()
        ai_events = conn.execute(
            """
            SELECT a.* FROM ai_events a
            JOIN (
              SELECT consultation_id, MAX(created_at) AS max_created
              FROM ai_events GROUP BY consultation_id
            ) latest
            ON a.consultation_id = latest.consultation_id AND a.created_at = latest.max_created
            """
        ).fetchall()
        users = conn.execute(
            "SELECT id, name, email, role, language, status, last_seen FROM admin_users ORDER BY role DESC, name ASC"
        ).fetchall()
        counts = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM consultations
            GROUP BY status
            """
        ).fetchall()
        current = row_dict(conn.execute("SELECT * FROM admin_users WHERE id = ?", (user["id"],)).fetchone()) or {}
        current.pop("password_hash", None)
        return {
            "current_user": current,
            "consultations": rows_dict(consultations),
            "ai_events": rows_dict(ai_events),
            "users": rows_dict(users),
            "metrics": {row["status"]: row["n"] for row in counts},
        }


@app.patch("/v1/admin/consultations/{consultation_id}")
def patch_consultation(
    consultation_id: str,
    payload: ConsultationPatch,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No changes supplied")
    if "assigned_admin_id" in changes and user["role"] != "supervisor":
        raise HTTPException(status_code=403, detail="Only supervisors can reassign cases")
    with connect() as conn:
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not consultation:
            raise HTTPException(status_code=404, detail="Consultation not found")
        if user["role"] == "admin" and consultation["assigned_admin_id"] not in (None, user["id"]):
            raise HTTPException(status_code=403, detail="This case is assigned to another admin")
        fields: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key == "needs_expert_review":
                value = 1 if value else 0
                if value and "status" not in changes:
                    fields.append("status = ?")
                    values.append("needs_expert_review")
            fields.append(f"{key} = ?")
            values.append(value)
        if changes.get("status") == "resolved":
            fields.append("resolved_at = ?")
            values.append(now())
        fields.append("updated_at = ?")
        values.append(now())
        values.append(consultation_id)
        conn.execute(f"UPDATE consultations SET {', '.join(fields)} WHERE id = ?", values)
        audit(
            conn,
            actor_type=user["role"],
            actor_id=user["id"],
            action="patch_consultation",
            consultation_id=consultation_id,
            details=changes,
        )
        auto_assign_waiting(conn)
        updated = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        return {"consultation": row_dict(updated)}


@app.get("/v1/admin/audit-log")
def audit_log(user: dict[str, Any] = Depends(require_supervisor)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 80").fetchall()
        return {"audit_log": rows_dict(rows)}


def frontend_index() -> FileResponse:
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_path)


@app.get("/", include_in_schema=False)
def serve_frontend_root() -> FileResponse:
    return frontend_index()


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend_path(full_path: str) -> FileResponse:
    reserved_prefixes = ("v1/", "health", "docs", "openapi.json", "redoc")
    if full_path.startswith(reserved_prefixes):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        dist_root = FRONTEND_DIST.resolve()
        candidate = (FRONTEND_DIST / full_path).resolve()
    except OSError:
        raise HTTPException(status_code=404, detail="Not found") from None

    if candidate.is_file() and (candidate == dist_root or dist_root in candidate.parents):
        return FileResponse(candidate)
    return frontend_index()


init_db()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8787, reload=True)
