import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { api, API_BASE } from './api';
import type { AuthState, Consultation, ConsultationStatus, QueuePayload } from './types';

type View = 'door' | 'queue';

const authKey = 'agent-door-operator-auth';

const statusText: Record<ConsultationStatus, string> = {
  waiting_human: 'Waiting',
  assigned: 'Assigned',
  active: 'Active',
  needs_expert_review: 'Review',
  resolved: 'Resolved',
};

const createConsultationCurl = `curl -X POST ${API_BASE}/v1/consultations \\
  -H "Content-Type: application/json" \\
  -d '{
    "customer_name": "Agent User",
    "customer_email": "user@example.com",
    "language": "en",
    "topic": "Login verification failed",
    "description": "The user cannot sign in after password reset and needs human support.",
    "source": "agent"
  }'`;

const getConsultationCurl = `curl ${API_BASE}/v1/consultations/{consultation_id}`;

const postMessageCurl = `curl -X POST ${API_BASE}/v1/consultations/{consultation_id}/messages \\
  -H "Content-Type: application/json" \\
  -d '{
    "content": "The user confirmed their account email.",
    "role": "agent",
    "language": "en"
  }'`;

const handoffCurl = `curl -X POST ${API_BASE}/v1/consultations/{consultation_id}/handoff \\
  -H "Content-Type: application/json" \\
  -d '{
    "reason": "The AI tool needs a human support admin to continue."
  }'`;

const discoveryLinks = [
  ['MCP SSE server', `${API_BASE}/mcp/sse`, 'Tool server for agents that can call MCP tools.'],
  ['Agent card', `${API_BASE}/.well-known/agent-card.json`, 'A2A-style discovery document.'],
  ['Agent card alias', `${API_BASE}/.well-known/agent.json`, 'Compatibility discovery path.'],
  ['Agent guide JSON', `${API_BASE}/agent-door.json`, 'Machine-readable queue workflow.'],
  ['LLMs text', `${API_BASE}/llms.txt`, 'Plain-text instructions for AI tools.'],
  ['Agent OpenAPI', `${API_BASE}/agent-openapi.json`, 'Public-only REST schema for tool builders.'],
];

const endpoints = [
  ['GET', '/.well-known/agent-card.json', 'Read public agent identity, skills, auth mode, and links.'],
  ['GET', '/agent-door.json', 'Read exact queue workflow and endpoint list.'],
  ['GET', '/llms.txt', 'Read short plain-text instructions.'],
  ['POST', '/v1/consultations', 'Create a queue ticket. No auth required.'],
  ['GET', '/v1/consultations/{id}', 'Read status, queue position, and AI triage. No auth required.'],
  ['GET', '/v1/consultations/{id}/messages', 'Read conversation messages. No auth required.'],
  ['POST', '/v1/consultations/{id}/messages', 'Post a user or agent update. No auth required.'],
  ['POST', '/v1/consultations/{id}/handoff', 'Request human support handoff. No auth required.'],
];

const mcpConfig = `{
  "mcpServers": {
    "support-door": {
      "url": "${API_BASE}/mcp/sse"
    }
  }
}`;

const mcpTools = [
  ['create_support_consultation', 'Create a support consultation and receive a queue number.'],
  ['get_support_consultation', 'Read status, queue position, and AI triage.'],
  ['list_consultation_messages', 'List all messages for a consultation.'],
  ['post_consultation_message', 'Post a customer or agent update.'],
  ['request_human_handoff', 'Escalate the consultation to the human queue.'],
  ['get_agent_door_guide', 'Read the machine-readable door guide through MCP.'],
];

const responseShape = `{
  "consultation": {
    "id": "uuid",
    "queue_number": "SUP-1001",
    "source": "agent",
    "status": "waiting_human",
    "queue_position": 1,
    "first_response_due_at": "2026-05-01T12:30:00+00:00"
  },
  "ai_event": {
    "classification": "Technical troubleshooting",
    "summary": "Agent User needs help with Login verification failed.",
    "confidence": 0.82,
    "suggested_reply": "Thanks. I have prepared the initial summary..."
  }
}`;

function App() {
  const [view, setView] = useState<View>(() => (window.location.pathname === '/queue' ? 'queue' : 'door'));

  function changeView(next: View) {
    setView(next);
    window.history.replaceState(null, '', next === 'queue' ? '/queue' : '/');
  }

  return (
    <main className="agent-doc">
      <nav className="top-nav" aria-label="Views">
        <button className={view === 'door' ? 'selected' : ''} onClick={() => changeView('door')}>
          Agent Door
        </button>
        <button className={view === 'queue' ? 'selected' : ''} onClick={() => changeView('queue')}>
          Local Queue Viewer
        </button>
      </nav>
      {view === 'door' ? <AgentDoor /> : <QueueViewer />}
    </main>
  );
}

function AgentDoor() {
  return (
    <>
      <header className="doc-header">
        <p className="kicker">Public Agent Door</p>
        <h1>Customer Support Queue API</h1>
        <p>
          This page is intentionally documentation-only. AI tools such as ChatGPT, Gemini, custom agents,
          and workflow automations can read the discovery files below, create a queue ticket, post updates,
          and request human handoff through REST.
        </p>
        <p>
          The public web surface is only this Agent Door. Customer portal and admin workspace screens are
          separated from this page; agents should use the public endpoints listed here.
        </p>
        <p className="note">
          Local URL: <code>{API_BASE}</code>. For public access, deploy with HTTPS and set{' '}
          <code>PUBLIC_BASE_URL=https://your-domain.com</code>.
        </p>
      </header>

      <section>
        <h2>1. Discovery URLs</h2>
        <div className="link-list">
          {discoveryLinks.map(([label, href, detail]) => (
            <a href={href} target="_blank" rel="noreferrer" key={href}>
              <strong>{label}</strong>
              <code>{href}</code>
              <span>{detail}</span>
            </a>
          ))}
        </div>
      </section>

      <section>
        <h2>2. MCP Tool Server</h2>
        <p>
          Agents that support MCP should connect to the SSE endpoint below. This is the preferred path when
          an AI tool can read pages but cannot directly post through a normal browser session.
        </p>
        <pre>{mcpConfig}</pre>
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {mcpTools.map(([tool, use]) => (
              <tr key={tool}>
                <td><code>{tool}</code></td>
                <td>{use}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>3. Queue Flow</h2>
        <ol>
          <li>Preferred: connect to <code>{`${API_BASE}/mcp/sse`}</code> and call <code>create_support_consultation</code>.</li>
          <li>Fetch <code>/.well-known/agent-card.json</code> or <code>/llms.txt</code>.</li>
          <li>Create a consultation with <code>POST /v1/consultations</code> and <code>source: "agent"</code>.</li>
          <li>Store <code>consultation.id</code> and show <code>consultation.queue_number</code> to the user.</li>
          <li>Poll <code>GET /v1/consultations/{'{id}'}</code> for status and queue position.</li>
          <li>Post updates with <code>POST /v1/consultations/{'{id}'}/messages</code>.</li>
          <li>Request human takeover with <code>POST /v1/consultations/{'{id}'}/handoff</code>.</li>
        </ol>
      </section>

      <section>
        <h2>4. Create Queue Ticket</h2>
        <pre>{createConsultationCurl}</pre>
      </section>

      <section>
        <h2>5. Expected Create Response</h2>
        <pre>{responseShape}</pre>
      </section>

      <section>
        <h2>6. Check Status</h2>
        <pre>{getConsultationCurl}</pre>
      </section>

      <section>
        <h2>7. Post Agent Message</h2>
        <pre>{postMessageCurl}</pre>
      </section>

      <section>
        <h2>8. Request Human Handoff</h2>
        <pre>{handoffCurl}</pre>
      </section>

      <section>
        <h2>9. Public Endpoint Index</h2>
        <table>
          <thead>
            <tr>
              <th>Method</th>
              <th>Path</th>
              <th>Use</th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map(([method, path, use]) => (
              <tr key={`${method}-${path}`}>
                <td><code>{method}</code></td>
                <td><code>{path}</code></td>
                <td>{use}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>10. Notes For Agents</h2>
        <ul>
          <li>Public consultation endpoints do not require an API key in this MVP.</li>
          <li>Admin endpoints under <code>/v1/admin/*</code> are private and require login.</li>
          <li>Valid languages are <code>en</code> and <code>ms</code>.</li>
          <li>Valid public message roles are <code>customer</code> and <code>agent</code>.</li>
          <li>Rate limiting is enabled on public write endpoints.</li>
        </ul>
      </section>
    </>
  );
}

function readStoredAuth(): AuthState | null {
  const raw = localStorage.getItem(authKey);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as AuthState;
    if (new Date(parsed.expires_at).getTime() < Date.now()) {
      localStorage.removeItem(authKey);
      return null;
    }
    return parsed;
  } catch {
    localStorage.removeItem(authKey);
    return null;
  }
}

function storeAuth(auth: AuthState | null) {
  if (auth) localStorage.setItem(authKey, JSON.stringify(auth));
  else localStorage.removeItem(authKey);
}

function QueueViewer() {
  const [auth, setAuthState] = useState<AuthState | null>(() => readStoredAuth());
  const [queue, setQueue] = useState<QueuePayload | null>(null);
  const [error, setError] = useState('');
  const [email, setEmail] = useState('admin@counter.local');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const setAuth = (next: AuthState | null) => {
    setAuthState(next);
    storeAuth(next);
  };

  const refreshQueue = useCallback(async () => {
    if (!auth) return;
    const payload = await api.queue(auth.token);
    setQueue(payload);
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    refreshQueue().catch((err) => setError(err instanceof Error ? err.message : 'Unable to load queue'));
    const timer = window.setInterval(() => {
      refreshQueue().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [auth, refreshQueue]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const next = await api.login(email, password);
      setAuth(next);
      await api.setStatus(next.token, 'online').catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to sign in');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (auth) await api.logout(auth.token).catch(() => undefined);
    setAuth(null);
    setQueue(null);
  }

  const visibleTickets = useMemo(() => {
    return (queue?.consultations ?? []).filter((ticket) => ticket.status !== 'resolved');
  }, [queue]);

  return (
    <>
      <header className="doc-header">
        <p className="kicker">Local Queue Viewer</p>
        <h1>Watch Deployed MCP Tickets From Localhost</h1>
        <p>
          Run this frontend locally with <code>VITE_API_BASE</code> set to the deployed Render URL.
          When an external agent calls the deployed MCP server, the ticket is stored in the deployed backend
          and appears here after login.
        </p>
        <p className="note">
          Current API target: <code>{API_BASE}</code>
        </p>
      </header>

      {!auth ? (
        <section>
          <h2>Admin Login</h2>
          <form className="login-form" onSubmit={login}>
            <label>
              Email
              <input value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            {error && <p className="error">{error}</p>}
            <button type="submit" disabled={busy}>{busy ? 'Signing in...' : 'Sign in'}</button>
          </form>
        </section>
      ) : (
        <>
          <section className="queue-toolbar">
            <div>
              <h2>{auth.user.name}</h2>
              <p>{auth.user.role} connected to <code>{API_BASE}</code></p>
            </div>
            <div className="toolbar-actions">
              <button onClick={() => refreshQueue().catch((err) => setError(err instanceof Error ? err.message : 'Refresh failed'))}>
                Refresh
              </button>
              <button onClick={logout}>Logout</button>
            </div>
          </section>

          {error && <p className="error">{error}</p>}

          <section>
            <h2>Queue Metrics</h2>
            <div className="metrics-grid">
              {(['waiting_human', 'assigned', 'active', 'needs_expert_review'] as ConsultationStatus[]).map((status) => (
                <div className="metric" key={status}>
                  <strong>{queue?.metrics[status] ?? 0}</strong>
                  <span>{statusText[status]}</span>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h2>Live Tickets</h2>
            <div className="ticket-grid">
              {visibleTickets.length === 0 && <p>No active tickets yet.</p>}
              {visibleTickets.map((ticket) => (
                <TicketCard ticket={ticket} key={ticket.id} />
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}

function TicketCard({ ticket }: { ticket: Consultation }) {
  return (
    <article className="ticket-card">
      <div className="ticket-head">
        <strong>{ticket.queue_number}</strong>
        <span className={`status ${ticket.status}`}>{statusText[ticket.status]}</span>
      </div>
      <h3>{ticket.customer_name}</h3>
      <p>{ticket.topic}</p>
      <dl>
        <div>
          <dt>Source</dt>
          <dd>{ticket.source}</dd>
        </div>
        <div>
          <dt>Language</dt>
          <dd>{ticket.language.toUpperCase()}</dd>
        </div>
        <div>
          <dt>Priority</dt>
          <dd>{ticket.priority}</dd>
        </div>
      </dl>
    </article>
  );
}

export default App;
