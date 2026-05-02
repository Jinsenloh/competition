import asyncio
import importlib
import os

from fastapi.testclient import TestClient
from fastmcp.client import Client


def load_app(tmp_path, public_base_url=None, serve_frontend=False):
    os.environ["SUPPORT_COUNTER_DB"] = str(tmp_path / "test.db")
    os.environ["SUPPORT_COUNTER_ADMIN_PASSWORD"] = "admin123"
    os.environ["SUPPORT_COUNTER_SUPERVISOR_PASSWORD"] = "super123"
    if serve_frontend:
        frontend_dist = tmp_path / "dist"
        (frontend_dist / "assets").mkdir(parents=True)
        (frontend_dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
        (frontend_dist / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")
        os.environ["FRONTEND_DIST_DIR"] = str(frontend_dist)
        os.environ["SERVE_FRONTEND"] = "true"
    else:
        os.environ["FRONTEND_DIST_DIR"] = str(tmp_path / "no-dist")
        os.environ["SERVE_FRONTEND"] = "false"
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
    assert root.json()["mcp"] == f"{public_url}/mcp/"

    mcp_redirect = client.get("/mcp", follow_redirects=False)
    assert mcp_redirect.status_code == 307
    assert mcp_redirect.headers["location"] == "/mcp/"

    mcp_get = client.get("/mcp/")
    assert mcp_get.status_code == 405

    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["url"] == public_url
    assert card.json()["authentication"]["required"] is False
    assert card.json()["extensions"][0]["url"] == f"{public_url}/mcp/"

    alias = client.get("/.well-known/agent.json")
    assert alias.status_code == 200
    assert alias.json()["documentationUrl"] == f"{public_url}/agent-door"

    guide = client.get("/agent-door.json")
    assert guide.status_code == 200
    assert guide.json()["discovery"]["mcp"] == f"{public_url}/mcp/"
    assert guide.json()["mcp"]["transport"] == "streamable-http"
    assert guide.json()["discovery"]["agent_openapi"] == f"{public_url}/agent-openapi.json"
    assert guide.json()["public_endpoints"][0]["operation_id"] == "createSupportConsultation"
    assert "create_support_consultation" in [tool["name"] for tool in guide.json()["mcp"]["tools"]]
    assert "continue_support_session" in [tool["name"] for tool in guide.json()["mcp"]["tools"]]

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

    async def run_mcp_flow():
        async with Client(server.support_mcp) as mcp_client:
            tool_names = {tool.name for tool in await mcp_client.list_tools()}
            assert {
                "create_support_consultation",
                "get_support_consultation",
                "list_consultation_messages",
                "find_support_consultations",
                "post_consultation_message",
                "continue_support_session",
                "request_human_handoff",
                "get_agent_door_guide",
            }.issubset(tool_names)

            mcp_created = await mcp_client.call_tool(
                "create_support_consultation",
                {
                    "customer_name": "MCP Agent",
                    "topic": "Corporate tax rebate question",
                    "description": "A company with 1B revenue needs human review for tax rebate eligibility.",
                    "language": "en",
                },
            )
            assert mcp_created.data["consultation"]["source"] == "agent"
            assert mcp_created.data["consultation"]["queue_number"].startswith("SUP-")
            assert mcp_created.data["consultation"]["priority"] == "high"
            assert "tax" in mcp_created.data["ai_event"]["classification"].lower()

            recovered = await mcp_client.call_tool(
                "find_support_consultations",
                {
                    "customer_name": "MCP Agent",
                    "limit": 1,
                },
            )
            assert recovered.data["count"] == 1
            assert recovered.data["consultations"][0]["id"] == mcp_created.data["consultation"]["id"]

            continued = await mcp_client.call_tool(
                "continue_support_session",
                {
                    "customer_name": "MCP Agent",
                    "content": "Please keep this same chat session active for follow-up tax questions.",
                    "language": "en",
                },
            )
            assert continued.data["consultation"]["id"] == mcp_created.data["consultation"]["id"]
            assert continued.data["message"]["role"] == "agent"
            assert len(continued.data["messages"]) == 2

    asyncio.run(run_mcp_flow())


def test_serves_frontend_without_hiding_mcp_or_api_routes(tmp_path):
    server, client = load_app(tmp_path, "https://support.example.com", serve_frontend=True)

    root = client.get("/")
    assert root.status_code == 200
    assert '<div id="root"></div>' in root.text

    spa_route = client.get("/queue")
    assert spa_route.status_code == 200
    assert '<div id="root"></div>' in spa_route.text

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log('ok');" in asset.text

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    guide = client.get("/agent-door.json")
    assert guide.status_code == 200
    assert guide.json()["mcp"]["url"] == "https://support.example.com/mcp/"

    mcp_redirect = client.get("/mcp", follow_redirects=False)
    assert mcp_redirect.status_code == 307
    assert mcp_redirect.headers["location"] == "/mcp/"
