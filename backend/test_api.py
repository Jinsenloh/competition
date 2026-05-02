import importlib
import os

from fastapi.testclient import TestClient


def load_app(tmp_path, public_base_url=None):
    os.environ["SUPPORT_COUNTER_DB"] = str(tmp_path / "test.db")
    os.environ["SUPPORT_COUNTER_ADMIN_PASSWORD"] = "admin123"
    os.environ["SUPPORT_COUNTER_SUPERVISOR_PASSWORD"] = "super123"
    if public_base_url:
        os.environ["PUBLIC_BASE_URL"] = public_base_url
    else:
        os.environ.pop("PUBLIC_BASE_URL", None)

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


def test_public_agent_door_discovery_and_agent_flow(tmp_path):
    public_url = "https://support.example.com"
    server, client = load_app(tmp_path, public_url)

    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["mode"] == "api-only"
    assert root.json()["mcp_sse"] == f"{public_url}/mcp/sse"

    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["url"] == public_url
    assert card.json()["authentication"]["required"] is False
    assert card.json()["extensions"][0]["url"] == f"{public_url}/mcp/sse"

    alias = client.get("/.well-known/agent.json")
    assert alias.status_code == 200
    assert alias.json()["documentationUrl"] == f"{public_url}/agent-door"

    guide = client.get("/agent-door.json")
    assert guide.status_code == 200
    assert guide.json()["discovery"]["mcp_sse"] == f"{public_url}/mcp/sse"
    assert guide.json()["discovery"]["agent_openapi"] == f"{public_url}/agent-openapi.json"
    assert guide.json()["public_endpoints"][0]["operation_id"] == "createSupportConsultation"
    assert "create_support_consultation" in [tool["name"] for tool in guide.json()["mcp"]["tools"]]

    agent_openapi = client.get("/agent-openapi.json")
    assert agent_openapi.status_code == 200
    assert "/v1/consultations" in agent_openapi.json()["paths"]
    assert "/v1/admin/queue" not in agent_openapi.json()["paths"]

    llms = client.get("/llms.txt")
    assert llms.status_code == 200
    assert f"Base URL: {public_url}" in llms.text

    openapi = client.get("/openapi.json").json()
    assert openapi["servers"][0]["url"] == public_url
    assert "Agent Discovery" in {tag["name"] for tag in openapi["tags"]}

    created = client.post(
        "/v1/consultations",
        json={
            "customer_name": "Agent User",
            "customer_email": "agent-user@example.com",
            "language": "en",
            "topic": "Login verification failed",
            "description": "The user cannot sign in after password reset and needs a remote support admin.",
            "source": "agent",
        },
    )
    assert created.status_code == 201
    consultation = created.json()["consultation"]
    assert consultation["source"] == "agent"
    assert consultation["queue_number"].startswith("SUP-")

    posted = client.post(
        f"/v1/consultations/{consultation['id']}/messages",
        json={"content": "The user confirmed the account email.", "role": "agent", "language": "en"},
    )
    assert posted.status_code == 201
    assert posted.json()["message"]["role"] == "agent"

    handoff = client.post(
        f"/v1/consultations/{consultation['id']}/handoff",
        json={"reason": "The AI tool needs a human support admin to continue."},
    )
    assert handoff.status_code == 200

    protected = client.get("/v1/admin/queue")
    assert protected.status_code == 401

    assert any(getattr(route, "path", "") == "/mcp" for route in server.app.routes)
    tool_names = {tool.name for tool in server.support_mcp._tool_manager.list_tools()}
    assert {
        "create_support_consultation",
        "get_support_consultation",
        "list_consultation_messages",
        "post_consultation_message",
        "request_human_handoff",
        "get_agent_door_guide",
    }.issubset(tool_names)

    mcp_created = server.support_mcp._tool_manager.get_tool("create_support_consultation").fn(
        customer_name="MCP Agent",
        topic="MCP ticket creation",
        description="An MCP client needs a human support queue number.",
        language="en",
    )
    assert mcp_created["consultation"]["source"] == "agent"
    assert mcp_created["consultation"]["queue_number"].startswith("SUP-")
