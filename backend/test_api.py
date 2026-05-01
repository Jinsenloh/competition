import importlib
import os

from fastapi.testclient import TestClient


def load_app(tmp_path):
    os.environ["SUPPORT_COUNTER_DB"] = str(tmp_path / "test.db")
    os.environ["SUPPORT_COUNTER_ADMIN_PASSWORD"] = "admin123"
    os.environ["SUPPORT_COUNTER_SUPERVISOR_PASSWORD"] = "super123"

    server = importlib.import_module("backend.server")
    importlib.reload(server)
    server.init_db()
    return server, TestClient(server.app)


def test_customer_ticket_admin_assignment_and_reply(tmp_path):
    server, client = load_app(tmp_path)

    login = client.post(
        "/v1/auth/login",
        json={"email": "admin@counter.local", "password": "admin123"},
    )
    assert login.status_code == 200
    token = login.json()["token"]

    created = client.post(
        "/v1/consultations",
        json={
            "customer_name": "Aisha Rahman",
            "customer_email": "aisha@example.com",
            "language": "en",
            "topic": "Login error",
            "description": "I cannot log in because the app keeps showing a failed verification error.",
            "source": "public",
        },
    )
    assert created.status_code == 201
    consultation = created.json()["consultation"]
    assert consultation["queue_number"].startswith("SUP-")
    assert consultation["assigned_admin_id"] == "adm-001"

    queue = client.get("/v1/admin/queue", headers={"Authorization": f"Bearer {token}"})
    assert queue.status_code == 200
    assert queue.json()["consultations"]

    reply = client.post(
        f"/v1/consultations/{consultation['id']}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "Please upload the rejection message and sample invoice.", "language": "en"},
    )
    assert reply.status_code == 201

    refreshed = client.get(f"/v1/consultations/{consultation['id']}")
    assert refreshed.json()["consultation"]["status"] == "active"


def test_admin_cannot_reassign_without_supervisor_role(tmp_path):
    server, client = load_app(tmp_path)

    admin_login = client.post(
        "/v1/auth/login",
        json={"email": "admin@counter.local", "password": "admin123"},
    ).json()
    consultation = client.post(
        "/v1/consultations",
        json={
            "customer_name": "Tan Mei",
            "language": "ms",
            "topic": "Refund request",
            "description": "Saya perlu bantuan untuk semak status refund dan pembayaran.",
            "source": "agent",
        },
    ).json()["consultation"]

    denied = client.patch(
        f"/v1/admin/consultations/{consultation['id']}",
        headers={"Authorization": f"Bearer {admin_login['token']}"},
        json={"assigned_admin_id": "adm-002"},
    )
    assert denied.status_code == 403

    supervisor_login = client.post(
        "/v1/auth/login",
        json={"email": "supervisor@counter.local", "password": "super123"},
    ).json()
    allowed = client.patch(
        f"/v1/admin/consultations/{consultation['id']}",
        headers={"Authorization": f"Bearer {supervisor_login['token']}"},
        json={"assigned_admin_id": "adm-002", "status": "assigned"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["consultation"]["assigned_admin_id"] == "adm-002"
