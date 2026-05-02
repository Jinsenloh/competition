# Deploy to Render Free

This deployment uses one Render Free Web Service for the React web app, the public API, and the FastMCP agent door.

## Free tier warning

Render Free is good for a demo, but local files are temporary. This app currently uses SQLite at `/tmp/agent_support_counter.db`, so ticket data resets when Render restarts, redeploys, or spins the service down after inactivity.

## 1. Push this project to GitHub

Render deploys from GitHub, GitLab, Bitbucket, a public Git URL, or a prebuilt Docker image. The easiest path is GitHub.

Commit and push these files:

```text
Dockerfile
.dockerignore
render.yaml
backend/
src/
package.json
package-lock.json
index.html
vite.config.*
tsconfig*.json
```

Do not push `node_modules/`, `.deploy/`, `.pytest-tmp/`, `.verification/`, `dist/`, `*.zip`, or local database files.

## 2. Create the Render service

1. Open Render Dashboard.
2. Choose **New** > **Blueprint**.
3. Connect the GitHub repo.
4. Render will read `render.yaml`.
5. When prompted for environment variables, enter:

```text
SUPPORT_COUNTER_ADMIN_PASSWORD=choose-a-demo-admin-password
SUPPORT_COUNTER_SUPERVISOR_PASSWORD=choose-a-demo-supervisor-password
PUBLIC_BASE_URL=https://your-render-or-custom-domain
SUPPORT_COUNTER_CORS_ORIGINS=https://your-render-or-custom-domain
FRONTEND_DIST_DIR=/app/dist
SERVE_FRONTEND=true
```

The login emails are:

```text
admin@counter.local
afiq@counter.local
supervisor@counter.local
```

## 3. Verify Render URL

After deploy completes, Render gives a URL like:

```text
https://agent-support-counter.onrender.com
```

Check:

```text
/health
/docs
/mcp/
/openapi.json
/agent-openapi.json
/.well-known/agent-card.json
/agent-door.json
/llms.txt
/
```

The root URL `/` should load the web app. The MCP endpoint remains:

Use this public MCP endpoint in Claude Connector, ChatGPT Developer Mode, or another remote MCP client:

```text
https://your-render-or-custom-domain/mcp/
```

Transport:

```text
Streamable HTTP
```

If you add a custom domain later, update `PUBLIC_BASE_URL` and `SUPPORT_COUNTER_CORS_ORIGINS` to that final HTTPS domain, then redeploy or restart the service.

For an existing Render service, open the service in Render and use **Manual Deploy** > **Deploy latest commit** after pushing to GitHub. Also confirm the service is using the `main` branch and Docker runtime, not a Vite/Node static service.

## 3.1. Use Local Queue Viewer During Development

The deployed MCP server and your local machine do not share SQLite data. If you still want to run the frontend locally while pointing it at the deployed backend:

```powershell
$env:VITE_API_BASE="https://your-render-or-custom-domain"
npm run dev -- --port 5173
```

Open:

```text
http://127.0.0.1:5173/queue
```

Sign in with the seeded admin email and the password you set in Render. Tickets created through `https://your-render-or-custom-domain/mcp/` will appear in this queue viewer.

If Render shows `Invalid Host header`, the service is running Vite/Node instead of the Docker FastAPI app. Recreate it as a Blueprint/Docker web service from this repo.

## 4. Point your Exabytes domain

In Render:

1. Open the Web Service.
2. Go to **Settings** > **Custom Domains**.
3. Add `www.yourdomain.com` first.
4. Render will show the DNS record to create.

In Exabytes DNS:

For `www.yourdomain.com`:

```text
Type: CNAME
Name/Host: www
Value/Target: your Render onrender.com hostname
```

For the root domain `yourdomain.com`, use one of these:

```text
Type: A
Name/Host: @
Value/Target: 216.24.57.1
```

Or, if Exabytes offers `ALIAS`, `ANAME`, or CNAME flattening:

```text
Type: ALIAS/ANAME
Name/Host: @
Value/Target: your Render onrender.com hostname
```

Remove any `AAAA` records for the domain while configuring Render.

Return to Render and click **Verify** beside the custom domain. SSL is issued automatically after DNS propagation.
