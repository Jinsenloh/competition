import { API_BASE } from './api';

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
  return (
    <main className="agent-doc">
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
        <h2>2. Queue Flow</h2>
        <ol>
          <li>Fetch <code>/.well-known/agent-card.json</code> or <code>/llms.txt</code>.</li>
          <li>Create a consultation with <code>POST /v1/consultations</code> and <code>source: "agent"</code>.</li>
          <li>Store <code>consultation.id</code> and show <code>consultation.queue_number</code> to the user.</li>
          <li>Poll <code>GET /v1/consultations/{'{id}'}</code> for status and queue position.</li>
          <li>Post updates with <code>POST /v1/consultations/{'{id}'}/messages</code>.</li>
          <li>Request human takeover with <code>POST /v1/consultations/{'{id}'}/handoff</code>.</li>
        </ol>
      </section>

      <section>
        <h2>3. Create Queue Ticket</h2>
        <pre>{createConsultationCurl}</pre>
      </section>

      <section>
        <h2>4. Expected Create Response</h2>
        <pre>{responseShape}</pre>
      </section>

      <section>
        <h2>5. Check Status</h2>
        <pre>{getConsultationCurl}</pre>
      </section>

      <section>
        <h2>6. Post Agent Message</h2>
        <pre>{postMessageCurl}</pre>
      </section>

      <section>
        <h2>7. Request Human Handoff</h2>
        <pre>{handoffCurl}</pre>
      </section>

      <section>
        <h2>8. Public Endpoint Index</h2>
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
        <h2>9. Notes For Agents</h2>
        <ul>
          <li>Public consultation endpoints do not require an API key in this MVP.</li>
          <li>Admin endpoints under <code>/v1/admin/*</code> are private and require login.</li>
          <li>Valid languages are <code>en</code> and <code>ms</code>.</li>
          <li>Valid public message roles are <code>customer</code> and <code>agent</code>.</li>
          <li>Rate limiting is enabled on public write endpoints.</li>
        </ul>
      </section>
    </main>
  );
}

export default App;
