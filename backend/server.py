from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SUPPORT_COUNTER_DB", ROOT / "agent_support_counter.db"))
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST_DIR", ROOT.parent / "dist"))
SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "false").lower() in {"1", "true", "yes"}
SESSION_HOURS = 10
APP_VERSION = "0.2.0"
PUBLIC_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("PUBLIC_RATE_LIMIT_WINDOW_SECONDS", "60"))
PUBLIC_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("PUBLIC_RATE_LIMIT_MAX_REQUESTS", "45"))
_public_rate_buckets: dict[str, list[float]] = {}


def normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


PUBLIC_BASE_URL = normalize_base_url(os.getenv("PUBLIC_BASE_URL"))


def public_base_url(request: Request) -> str:
    return PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


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


def request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def client_hash(request: Request) -> str:
    return hashlib.sha256(f"public-agent-door:{request_ip(request)}".encode("utf-8")).hexdigest()[:24]


def enforce_public_rate_limit(request: Request) -> str:
    return enforce_public_rate_limit_key(client_hash(request))


def enforce_public_rate_limit_key(key: str) -> str:
    current = time.monotonic()
    recent = [
        timestamp
        for timestamp in _public_rate_buckets.get(key, [])
        if current - timestamp < PUBLIC_RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(recent) >= PUBLIC_RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many public requests. Please wait and try again.",
        )
    recent.append(current)
    _public_rate_buckets[key] = recent
    return key


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
    email: str = Field(examples=["admin@counter.local"])
    password: str = Field(examples=["admin123"])


class StatusRequest(BaseModel):
    status: Literal["online", "away", "offline"]


class ConsultationCreate(BaseModel):
    customer_name: str = Field(
        min_length=2,
        max_length=120,
        description="Human customer name or the display name supplied by the calling AI tool.",
        examples=["Agent User"],
    )
    customer_email: str | None = Field(default=None, description="Optional customer contact email.")
    language: Literal["en", "ms"] = "en"
    topic: str = Field(min_length=3, max_length=160, examples=["Login verification failed"])
    description: str = Field(
        min_length=8,
        max_length=4000,
        description="Initial support issue or structured summary from the external AI tool.",
        examples=["The user cannot sign in after password reset and needs a human support admin."],
    )
    source: Literal["public", "agent"] = Field(
        default="public",
        description="Use 'agent' when ChatGPT, Gemini, or another AI tool creates the consultation for a user.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "customer_name": "Agent User",
                    "customer_email": "user@example.com",
                    "language": "en",
                    "topic": "Login verification failed",
                    "description": "The user cannot sign in after password reset and needs a human support admin.",
                    "source": "agent",
                }
            ]
        }
    }


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000, examples=["The user has confirmed their account email."])
    role: Literal["customer", "agent"] = Field(
        default="customer",
        description="Use 'agent' when an external AI tool is speaking on behalf of the user.",
    )
    language: Literal["en", "ms"] = "en"

    model_config = {
        "json_schema_extra": {
            "examples": [{"content": "The user has confirmed their account email.", "role": "agent", "language": "en"}]
        }
    }


class HandoffRequest(BaseModel):
    reason: str = Field(
        default="AI confidence below support threshold",
        max_length=500,
        description="Why the AI tool wants a human support admin to take over.",
    )


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
    elif any(k in text for k in ["tax", "rebate", "deduction", "1b", "revenue", "corporate tax", "lhdn", "irs"]):
        classification = "Tax or compliance enquiry"
        priority = "high"
        confidence = 0.7
        docs = [
            "Company jurisdiction and tax residency",
            "Latest audited revenue and profit figures",
            "Entity type and registration number",
            "Expense, incentive, and rebate documents",
            "Preferred tax advisor or finance contact",
        ]
        escalation = "Tax and compliance questions require review by a qualified human expert."
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


def create_consultation_record(payload: ConsultationCreate, *, ip_hash: str | None = None) -> dict[str, Any]:
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
            details={
                "queue_number": queue_number,
                "classification": triage["classification"],
                "ip_hash": ip_hash,
                "source": payload.source,
            },
        )
        auto_assign_waiting(conn)
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        ai_event = conn.execute("SELECT * FROM ai_events WHERE consultation_id = ?", (consultation_id,)).fetchone()
        return {"consultation": row_dict(consultation), "ai_event": row_dict(ai_event)}


def get_consultation_payload(consultation_id: str) -> dict[str, Any]:
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


def list_messages_payload(consultation_id: str) -> dict[str, Any]:
    with connect() as conn:
        exists = conn.execute("SELECT id FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Consultation not found")
        messages = conn.execute(
            "SELECT * FROM messages WHERE consultation_id = ? ORDER BY created_at ASC",
            (consultation_id,),
        ).fetchall()
        return {"messages": rows_dict(messages)}


def post_public_message_record(
    consultation_id: str,
    payload: MessageCreate,
    *,
    ip_hash: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        consultation = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        if not consultation:
            raise HTTPException(status_code=404, detail="Consultation not found")
        message_id = str(uuid.uuid4())
        sender_name = "External Agent" if payload.role == "agent" else "Customer"
        conn.execute(
            """
            INSERT INTO messages (id, consultation_id, role, sender_name, content, language, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, consultation_id, payload.role, sender_name, payload.content, payload.language, now()),
        )
        conn.execute("UPDATE consultations SET updated_at = ? WHERE id = ?", (now(), consultation_id))
        audit(
            conn,
            actor_type=payload.role,
            actor_id=None,
            action="post_message",
            consultation_id=consultation_id,
            details={"role": payload.role, "status_after": consultation["status"], "ip_hash": ip_hash},
        )
        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return {"message": row_dict(message)}


def request_handoff_record(consultation_id: str, reason: str, *, ip_hash: str | None = None) -> dict[str, Any]:
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
            (str(uuid.uuid4()), consultation_id, f"AI requested handoff for {consultation['queue_number']}.", reason, now()),
        )
        audit(
            conn,
            actor_type="ai",
            actor_id=None,
            action="handoff",
            consultation_id=consultation_id,
            details={"reason": reason, "ip_hash": ip_hash},
        )
        auto_assign_waiting(conn)
        updated = conn.execute("SELECT * FROM consultations WHERE id = ?", (consultation_id,)).fetchone()
        return {"consultation": row_dict(updated)}


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
    origins = ["http://127.0.0.1:5173", "http://localhost:5173"]
    raw = os.getenv("SUPPORT_COUNTER_CORS_ORIGINS")
    if raw:
        origins.extend(origin.strip() for origin in raw.split(",") if origin.strip())
    if PUBLIC_BASE_URL and PUBLIC_BASE_URL not in origins:
        origins.append(PUBLIC_BASE_URL)
    return list(dict.fromkeys(origins))


app_config: dict[str, Any] = {
    "title": "Public Agent Customer Support Door",
    "version": APP_VERSION,
    "description": (
        "A public customer support queue that can be used by humans through the web app "
        "and by AI tools through REST/OpenAPI. External agents can create a consultation, "
        "receive a queue number, post messages, check status, and request human handoff."
    ),
    "contact": {"name": "Agent Support Counter"},
    "openapi_tags": [
        {"name": "Agent Discovery", "description": "Public discovery documents for AI tools and agent networks."},
        {"name": "Public Consultations", "description": "No-key queue and consultation endpoints for humans and AI tools."},
        {"name": "Admin Auth", "description": "Login and session endpoints for remote support admins."},
        {"name": "Admin Queue", "description": "Authenticated queue, assignment, supervisor, and audit endpoints."},
        {"name": "System", "description": "Health checks."},
    ],
}
if PUBLIC_BASE_URL:
    app_config["servers"] = [{"url": PUBLIC_BASE_URL, "description": "Configured public HTTPS server"}]


support_mcp = FastMCP(
    "Public Agent Customer Support Door",
    instructions=(
        "Use these tools to create and manage public customer support consultations. "
        "Create a consultation first, persist the returned consultation.id, show the queue_number to the user, "
        "then use the id to check status, list messages, post updates, or request human handoff."
    ),
)


@support_mcp.tool(
    name="create_support_consultation",
    description="Create a public support consultation and receive a queue number.",
)
def mcp_create_support_consultation(
    customer_name: str,
    topic: str,
    description: str,
    customer_email: str | None = None,
    language: Literal["en", "ms"] = "en",
) -> dict[str, Any]:
    ip_hash = enforce_public_rate_limit_key("mcp-public")
    payload = ConsultationCreate(
        customer_name=customer_name,
        customer_email=customer_email,
        language=language,
        topic=topic,
        description=description,
        source="agent",
    )
    return create_consultation_record(payload, ip_hash=ip_hash)


@support_mcp.tool(
    name="get_support_consultation",
    description="Read a consultation's current status, queue number, queue position, and AI triage summary.",
)
def mcp_get_support_consultation(consultation_id: str) -> dict[str, Any]:
    return get_consultation_payload(consultation_id)


@support_mcp.tool(
    name="list_consultation_messages",
    description="List all messages for a consultation in chronological order.",
)
def mcp_list_consultation_messages(consultation_id: str) -> dict[str, Any]:
    return list_messages_payload(consultation_id)


@support_mcp.tool(
    name="post_consultation_message",
    description="Post an update from the external agent or customer into an existing consultation.",
)
def mcp_post_consultation_message(
    consultation_id: str,
    content: str,
    role: Literal["customer", "agent"] = "agent",
    language: Literal["en", "ms"] = "en",
) -> dict[str, Any]:
    ip_hash = enforce_public_rate_limit_key("mcp-public")
    payload = MessageCreate(content=content, role=role, language=language)
    return post_public_message_record(consultation_id, payload, ip_hash=ip_hash)


@support_mcp.tool(
    name="request_human_handoff",
    description="Move a consultation back to the human support queue and explain why human help is needed.",
)
def mcp_request_human_handoff(
    consultation_id: str,
    reason: str = "The AI tool needs a human support admin to continue.",
) -> dict[str, Any]:
    ip_hash = enforce_public_rate_limit_key("mcp-public")
    return request_handoff_record(consultation_id, reason, ip_hash=ip_hash)


@support_mcp.tool(
    name="get_agent_door_guide",
    description="Return the machine-readable guide for this support door, including REST and MCP discovery URLs.",
)
def mcp_get_agent_door_guide(base_url: str | None = None) -> dict[str, Any]:
    return agent_door_payload(normalize_base_url(base_url) or PUBLIC_BASE_URL or "http://127.0.0.1:8787")


mcp_app = support_mcp.http_app(path="/", stateless_http=True, transport="streamable-http")


@asynccontextmanager
async def app_lifespan(app_instance: FastAPI):
    init_db()
    async with mcp_app.lifespan(app_instance):
        yield


app = FastAPI(**app_config, lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if SERVE_FRONTEND and (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/mcp", include_in_schema=False)
def redirect_mcp() -> RedirectResponse:
    return RedirectResponse(url="/mcp/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


app.mount("/mcp", mcp_app, name="mcp-streamable-http")


def agent_card_payload(base_url: str) -> dict[str, Any]:
    return {
        "schema_version": "0.3",
        "protocol": "a2a-discovery-rest-bridge",
        "name": "Public Agent Customer Support Door",
        "description": (
            "A public support counter where AI tools can take a queue number for a user, "
            "continue the consultation by REST, and request human support handoff."
        ),
        "url": base_url,
        "version": APP_VERSION,
        "documentationUrl": f"{base_url}/agent-door",
        "provider": {"organization": "Agent Support Counter", "url": base_url},
        "authentication": {
            "required": False,
            "schemes": ["none"],
            "note": "Public consultation endpoints require no API key for the MVP. Admin endpoints require login.",
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
            "humanHandoff": True,
            "queueNumber": True,
        },
        "skills": [
            {
                "id": "take_support_number",
                "name": "Take a support queue number",
                "description": "Create a consultation for a user and receive a queue number.",
                "tags": ["customer-support", "queue", "rest"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "examples": [
                    "Create a support ticket for a user who cannot log in.",
                    "Ask for human support after AI triage is not enough.",
                ],
            },
            {
                "id": "consult_with_human_support",
                "name": "Consult with human support",
                "description": "Post follow-up messages, read queue status, and request handoff to a remote admin.",
                "tags": ["human-handoff", "support-chat", "status"],
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
        ],
        "extensions": [
            {"id": "mcp-streamable-http", "name": "MCP Streamable HTTP tool server", "url": f"{base_url}/mcp/"},
            {"id": "agent-openapi", "name": "Public agent OpenAPI contract", "url": f"{base_url}/agent-openapi.json"},
            {"id": "agent-door", "name": "Agent workflow guide", "url": f"{base_url}/agent-door.json"},
            {"id": "llms", "name": "LLM text guide", "url": f"{base_url}/llms.txt"},
        ],
    }


def agent_door_payload(base_url: str) -> dict[str, Any]:
    create_body = {
        "customer_name": "Agent User",
        "customer_email": "user@example.com",
        "language": "en",
        "topic": "Login verification failed",
        "description": "The user cannot sign in after password reset and needs a human support admin.",
        "source": "agent",
    }
    return {
        "schema_version": "2026-05-01",
        "name": "Public Agent Door For Customer Support",
        "base_url": base_url,
        "purpose": (
            "Let humans and AI tools enter the same remote customer support queue. "
            "The AI tool can create the ticket, keep the user updated, and request human handoff."
        ),
        "auth": {
            "public_consultation_endpoints": "none",
            "admin_endpoints": "bearer session token from /v1/auth/login",
            "production_note": "Add API keys or OAuth before handling sensitive public production traffic.",
        },
        "discovery": {
            "agent_card": f"{base_url}/.well-known/agent-card.json",
            "agent_card_alias": f"{base_url}/.well-known/agent.json",
            "mcp": f"{base_url}/mcp/",
            "agent_openapi": f"{base_url}/agent-openapi.json",
            "llms_txt": f"{base_url}/llms.txt",
        },
        "mcp": {
            "transport": "streamable-http",
            "url": f"{base_url}/mcp/",
            "tools": [
                {
                    "name": "create_support_consultation",
                    "description": "Create a support consultation and receive a queue number.",
                },
                {
                    "name": "get_support_consultation",
                    "description": "Read status, queue position, and AI triage for a consultation.",
                },
                {
                    "name": "list_consultation_messages",
                    "description": "List conversation messages for a consultation.",
                },
                {
                    "name": "post_consultation_message",
                    "description": "Post a customer or agent update.",
                },
                {
                    "name": "request_human_handoff",
                    "description": "Escalate the consultation to the human support queue.",
                },
                {
                    "name": "get_agent_door_guide",
                    "description": "Read this machine-readable guide through MCP.",
                },
            ],
            "client_config_example": {"mcpServers": {"support-door": {"url": f"{base_url}/mcp/"}}},
        },
        "workflow": [
            {
                "step": 1,
                "name": "Create consultation",
                "method": "POST",
                "path": "/v1/consultations",
                "result": "Returns consultation.id and consultation.queue_number.",
            },
            {
                "step": 2,
                "name": "Check queue and status",
                "method": "GET",
                "path": "/v1/consultations/{id}",
                "result": "Returns status, queue_position, SLA due time, and latest AI triage event.",
            },
            {
                "step": 3,
                "name": "Send updates",
                "method": "POST",
                "path": "/v1/consultations/{id}/messages",
                "result": "Adds a customer or agent message to the consultation.",
            },
            {
                "step": 4,
                "name": "Request human handoff",
                "method": "POST",
                "path": "/v1/consultations/{id}/handoff",
                "result": "Moves the consultation back to the human support queue.",
            },
        ],
        "public_endpoints": [
            {
                "method": "POST",
                "path": "/v1/consultations",
                "operation_id": "createSupportConsultation",
                "auth": "none",
            },
            {
                "method": "GET",
                "path": "/v1/consultations/{id}",
                "operation_id": "getSupportConsultation",
                "auth": "none",
            },
            {
                "method": "GET",
                "path": "/v1/consultations/{id}/messages",
                "operation_id": "listConsultationMessages",
                "auth": "none",
            },
            {
                "method": "POST",
                "path": "/v1/consultations/{id}/messages",
                "operation_id": "postConsultationMessage",
                "auth": "none for public/agent messages; bearer token for admin replies",
            },
            {
                "method": "POST",
                "path": "/v1/consultations/{id}/handoff",
                "operation_id": "requestHumanHandoff",
                "auth": "none",
            },
        ],
        "status_values": {
            "waiting_human": "Waiting for an available support admin.",
            "assigned": "Assigned to an admin but not yet active.",
            "active": "Admin is actively handling the consultation.",
            "needs_expert_review": "Marked for specialist review.",
            "resolved": "Consultation is closed.",
        },
        "example_create_request": create_body,
        "code_examples": {
            "create_consultation": (
                f"curl -X POST {base_url}/v1/consultations "
                "-H 'Content-Type: application/json' "
                f"-d '{json.dumps(create_body)}'"
            ),
            "get_consultation": f"curl {base_url}/v1/consultations/{{consultation_id}}",
            "post_message": (
                f"curl -X POST {base_url}/v1/consultations/{{consultation_id}}/messages "
                "-H 'Content-Type: application/json' "
                "-d '{\"content\":\"The user confirmed their account email.\",\"role\":\"agent\",\"language\":\"en\"}'"
            ),
            "request_handoff": (
                f"curl -X POST {base_url}/v1/consultations/{{consultation_id}}/handoff "
                "-H 'Content-Type: application/json' "
                "-d '{\"reason\":\"The AI tool needs a human support admin to continue.\"}'"
            ),
        },
        "agent_instructions": [
            "Use source='agent' when creating a consultation for a user.",
            "Persist consultation.id. It is required for status, messages, and handoff.",
            "Show consultation.queue_number to the user as their support number.",
            "Poll the status endpoint conservatively. Do not spam public write endpoints.",
            "Do not call /v1/admin/* endpoints; they are for authenticated human support admins.",
        ],
        "rate_limits": {
            "public_write_window_seconds": PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
            "public_write_max_requests": PUBLIC_RATE_LIMIT_MAX_REQUESTS,
        },
    }


@app.get(
    "/.well-known/agent-card.json",
    tags=["Agent Discovery"],
    summary="Get the public A2A-style agent card",
    operation_id="getAgentCard",
)
def get_agent_card(request: Request) -> dict[str, Any]:
    return agent_card_payload(public_base_url(request))


@app.get("/.well-known/agent.json", include_in_schema=False)
def get_agent_card_alias(request: Request) -> dict[str, Any]:
    return agent_card_payload(public_base_url(request))


@app.get(
    "/agent-door.json",
    tags=["Agent Discovery"],
    summary="Get the machine-readable support queue workflow",
    operation_id="getAgentDoorGuide",
)
def get_agent_door(request: Request) -> dict[str, Any]:
    return agent_door_payload(public_base_url(request))


@app.get(
    "/llms.txt",
    response_class=PlainTextResponse,
    tags=["Agent Discovery"],
    summary="Get a short LLM-readable guide",
    operation_id="getLlmsTxt",
)
def get_llms_txt(request: Request) -> str:
    base_url = public_base_url(request)
    create_body = json.dumps(
        {
            "customer_name": "Agent User",
            "customer_email": "user@example.com",
            "language": "en",
            "topic": "Login verification failed",
            "description": "The user cannot sign in after password reset and needs a human support admin.",
            "source": "agent",
        },
        indent=2,
    )
    return "\n".join(
        [
            "# Public Agent Customer Support Door",
            "",
            f"Base URL: {base_url}",
            "Purpose: let AI tools create a support consultation, receive a queue number, post updates, and request human handoff.",
            "",
            "Discovery:",
            f"- MCP Streamable HTTP tool server: {base_url}/mcp/",
            f"- Agent Card: {base_url}/.well-known/agent-card.json",
            f"- Agent Guide JSON: {base_url}/agent-door.json",
            f"- Public Agent OpenAPI: {base_url}/agent-openapi.json",
            f"- Full developer Swagger docs: {base_url}/docs",
            "",
            "Queue workflow:",
            "1. POST /v1/consultations with source='agent'.",
            "2. Read consultation.id and consultation.queue_number from the response.",
            "3. GET /v1/consultations/{id} to check status and queue position.",
            "4. POST /v1/consultations/{id}/messages to add user or agent updates.",
            "5. POST /v1/consultations/{id}/handoff when a human support admin is needed.",
            "",
            "Create consultation example:",
            f"curl -X POST {base_url}/v1/consultations \\",
            '  -H "Content-Type: application/json" \\',
            f"  -d '{create_body}'",
            "",
            "Post message example:",
            f"curl -X POST {base_url}/v1/consultations/{{consultation_id}}/messages \\",
            '  -H "Content-Type: application/json" \\',
            '  -d \'{"content":"The user confirmed their account email.","role":"agent","language":"en"}\'',
            "",
            "Request handoff example:",
            f"curl -X POST {base_url}/v1/consultations/{{consultation_id}}/handoff \\",
            '  -H "Content-Type: application/json" \\',
            '  -d \'{"reason":"The AI tool needs a human support admin to continue."}\'',
            "",
            "MCP Streamable HTTP tools:",
            "- create_support_consultation",
            "- get_support_consultation",
            "- list_consultation_messages",
            "- post_consultation_message",
            "- request_human_handoff",
            "- get_agent_door_guide",
            "",
            "Example MCP server config:",
            f'{{"mcpServers":{{"support-door":{{"url":"{base_url}/mcp/"}}}}}}',
            "",
            "Public consultation endpoints require no API key for this MVP. Admin endpoints require login.",
        ]
    )


@app.get(
    "/agent-openapi.json",
    tags=["Agent Discovery"],
    summary="Get public-only OpenAPI for external AI tools",
    operation_id="getAgentOpenApi",
)
def get_agent_openapi() -> dict[str, Any]:
    schema = json.loads(json.dumps(app.openapi()))
    allowed_paths = {
        "/.well-known/agent-card.json",
        "/agent-door.json",
        "/agent-openapi.json",
        "/llms.txt",
        "/health",
        "/v1/consultations",
    }
    schema["paths"] = {
        path: methods
        for path, methods in schema.get("paths", {}).items()
        if path in allowed_paths or path.startswith("/v1/consultations/")
    }
    schema["tags"] = [
        tag
        for tag in schema.get("tags", [])
        if tag.get("name") in {"Agent Discovery", "Public Consultations", "System"}
    ]
    schema["info"] = {
        **schema.get("info", {}),
        "title": "Public Agent Customer Support Door - Agent API",
        "description": (
            "Public-only OpenAPI contract for AI tools. Use this file to create consultations, "
            "check queue status, post messages, and request human handoff. Admin routes are excluded."
        ),
    }
    return schema


@app.get("/health", tags=["System"], summary="Health check", operation_id="getHealth")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now()}


@app.post("/v1/auth/login", tags=["Admin Auth"], summary="Log in a support admin", operation_id="loginAdmin")
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


@app.get("/v1/auth/me", tags=["Admin Auth"], summary="Get current admin session", operation_id="getCurrentAdmin")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    user.pop("password_hash", None)
    return {"user": user}


@app.post("/v1/auth/logout", status_code=204, tags=["Admin Auth"], summary="Log out admin session", operation_id="logoutAdmin")
def logout(response: Response, user: dict[str, Any] = Depends(current_user), authorization: str | None = Header(default=None)) -> Response:
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.execute("UPDATE admin_users SET status = 'offline', last_seen = ? WHERE id = ?", (now(), user["id"]))
    response.status_code = 204
    return response


@app.patch("/v1/admin/me/status", tags=["Admin Queue"], summary="Set admin availability status", operation_id="setAdminStatus")
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


@app.post(
    "/v1/consultations",
    status_code=201,
    tags=["Public Consultations"],
    summary="Create a public support consultation and receive a queue number",
    description=(
        "Public no-key endpoint for humans and external AI tools. "
        "Use source='agent' when ChatGPT, Gemini, or another agent creates the request for a user."
    ),
    operation_id="createSupportConsultation",
)
def create_consultation(payload: ConsultationCreate, request: Request) -> dict[str, Any]:
    ip_hash = enforce_public_rate_limit(request)
    return create_consultation_record(payload, ip_hash=ip_hash)


@app.get(
    "/v1/consultations/{consultation_id}",
    tags=["Public Consultations"],
    summary="Read consultation status and queue position",
    operation_id="getSupportConsultation",
)
def get_consultation(consultation_id: str) -> dict[str, Any]:
    return get_consultation_payload(consultation_id)


@app.get(
    "/v1/consultations/{consultation_id}/messages",
    tags=["Public Consultations"],
    summary="List consultation messages",
    operation_id="listConsultationMessages",
)
def get_messages(consultation_id: str) -> dict[str, Any]:
    return list_messages_payload(consultation_id)


@app.post(
    "/v1/consultations/{consultation_id}/messages",
    status_code=201,
    tags=["Public Consultations"],
    summary="Post a customer, agent, or admin message",
    description=(
        "Public callers can post customer or agent messages without an API key. "
        "If a valid admin bearer token is supplied, the message is recorded as an admin reply."
    ),
    operation_id="postConsultationMessage",
)
def post_message(
    consultation_id: str,
    payload: MessageCreate,
    request: Request,
    user: dict[str, Any] | None = Depends(optional_user),
) -> dict[str, Any]:
    if not user:
        ip_hash = enforce_public_rate_limit(request)
        return post_public_message_record(consultation_id, payload, ip_hash=ip_hash)
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
            details={"role": role, "status_after": new_status, "ip_hash": None},
        )
        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return {"message": row_dict(message)}


@app.post(
    "/v1/consultations/{consultation_id}/handoff",
    tags=["Public Consultations"],
    summary="Request human support handoff",
    operation_id="requestHumanHandoff",
)
def handoff(consultation_id: str, payload: HandoffRequest, request: Request) -> dict[str, Any]:
    ip_hash = enforce_public_rate_limit(request)
    return request_handoff_record(consultation_id, payload.reason, ip_hash=ip_hash)


@app.get("/v1/admin/queue", tags=["Admin Queue"], summary="Get authenticated admin queue", operation_id="getAdminQueue")
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


@app.patch(
    "/v1/admin/consultations/{consultation_id}",
    tags=["Admin Queue"],
    summary="Update consultation status, assignment, or priority",
    operation_id="updateAdminConsultation",
)
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


@app.get("/v1/admin/audit-log", tags=["Admin Queue"], summary="Get supervisor audit log", operation_id="getAuditLog")
def audit_log(user: dict[str, Any] = Depends(require_supervisor)) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 80").fetchall()
        return {"audit_log": rows_dict(rows)}


def frontend_index() -> FileResponse:
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_path)


@app.get("/", include_in_schema=False, response_model=None)
def serve_root(request: Request) -> dict[str, Any] | FileResponse:
    if SERVE_FRONTEND:
        return frontend_index()
    base_url = public_base_url(request)
    return {
        "name": "Public Agent Customer Support Door",
        "mode": "api-only",
        "health": f"{base_url}/health",
        "mcp": f"{base_url}/mcp/",
        "agent_door": f"{base_url}/agent-door.json",
        "llms": f"{base_url}/llms.txt",
        "openapi": f"{base_url}/agent-openapi.json",
        "local_web_note": "Run the React web locally with VITE_API_BASE set to this deployed URL.",
    }


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend_path(full_path: str) -> FileResponse:
    reserved_prefixes = (
        "v1/",
        "health",
        "docs",
        "openapi.json",
        "redoc",
        "mcp",
        "mcp/",
        ".well-known/",
        "agent-door.json",
        "agent-openapi.json",
        "llms.txt",
    )
    if full_path.startswith(reserved_prefixes):
        raise HTTPException(status_code=404, detail="Not found")

    if not SERVE_FRONTEND:
        raise HTTPException(status_code=404, detail="API-only deployment")

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
