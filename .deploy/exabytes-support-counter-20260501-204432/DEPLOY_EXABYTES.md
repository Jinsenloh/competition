# Deploy to Exabytes cPanel

This project is a Vite React frontend plus a FastAPI backend. The simplest Exabytes setup is to run the FastAPI app as the cPanel Python application and let FastAPI serve the built `dist` frontend from the same domain.

## 1. Build locally

From this project folder:

```powershell
npm run build
```

The production frontend will be created in `dist/`.

## 2. Upload these files

Upload the project to the cPanel application folder, for example `support-counter/`:

```text
backend/
dist/
passenger_wsgi.py
DEPLOY_EXABYTES.md
```

Do not upload `node_modules/`, `.pytest-tmp/`, `.verification/`, `__pycache__/`, `.pytest_cache/`, or local `.db` files unless you intentionally want to keep your local test data.

## 3. Create the Python app in cPanel

In Exabytes cPanel, open **Setup Python App** and create an application.

Suggested values:

```text
Python version: 3.11 or newer if available
Application root: support-counter
Application URL: your domain or subdomain
Application startup file: leave blank if Exabytes auto-creates passenger_wsgi.py
Application Entry point: leave blank if Exabytes auto-creates passenger_wsgi.py
```

If the panel requires values, use:

```text
Application startup file: passenger_wsgi.py
Application Entry point: application
```

## 4. Install backend dependencies

Open the Terminal for the Python app, activate the virtual environment using the command shown in cPanel, then run:

```bash
cd ~/support-counter
pip install -r backend/requirements.txt
```

## 5. Set environment variables

In the Python app settings, add:

```text
FRONTEND_DIST_DIR=/home/YOUR_CPANEL_USER/support-counter/dist
SUPPORT_COUNTER_DB=/home/YOUR_CPANEL_USER/support-counter/backend/agent_support_counter.db
SUPPORT_COUNTER_ADMIN_PASSWORD=choose-a-strong-admin-password
SUPPORT_COUNTER_SUPERVISOR_PASSWORD=choose-a-strong-supervisor-password
```

If the frontend and API are on different domains, also add:

```text
SUPPORT_COUNTER_CORS_ORIGINS=https://yourdomain.com
```

For the recommended same-domain setup, no `VITE_API_BASE` value is needed before building.

## 6. Restart and verify

Restart the app from cPanel, then check:

```text
https://yourdomain.com/health
https://yourdomain.com/docs
https://yourdomain.com/
```

Use these seeded login emails with the passwords you set in the environment variables:

```text
admin@counter.local
afiq@counter.local
supervisor@counter.local
```

## DNS note

If the domain is only registered at Exabytes but not pointed to this hosting account yet, update the DNS in Exabytes Client Area or cPanel Zone Editor. Point the domain or subdomain to the hosting server as instructed by Exabytes, then wait for DNS propagation.
