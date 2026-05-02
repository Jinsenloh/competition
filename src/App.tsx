import {
  AlertTriangle,
  ArrowRightLeft,
  Bot,
  CheckCircle2,
  ClipboardList,
  FileText,
  Gauge,
  Home,
  LogOut,
  MessageSquareText,
  MonitorCheck,
  PlayCircle,
  Send,
  ShieldCheck,
  UserCheck,
  Wifi,
} from 'lucide-react';
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { api, API_BASE } from './api';
import { ConsultationRoom3D } from './components/ConsultationRoom3D';
import type { AiEvent, AuthState, Consultation, ConsultationStatus, Language, Message, QueuePayload } from './types';

type View = 'public' | 'admin' | 'docs';

const authKey = 'agent-support-counter-auth';
const showDemoCredentials = import.meta.env.DEV || import.meta.env.VITE_SHOW_DEMO_CREDENTIALS === 'true';

const statusText: Record<ConsultationStatus, string> = {
  waiting_human: 'Waiting human',
  assigned: 'Assigned',
  active: 'Active',
  needs_expert_review: 'Specialist review',
  resolved: 'Resolved',
};

const publicCopy = {
  en: {
    title: 'Take a number for customer support',
    subtitle: 'Submit your issue, receive a queue number, and continue the chat while a remote support admin reviews your case.',
    name: 'Full name',
    email: 'Email',
    topic: 'Topic',
    details: 'Issue details',
    submit: 'Get queue number',
    chat: 'Send update',
    placeholder: 'Describe your customer support issue clearly.',
  },
  ms: {
    title: 'Ambil nombor giliran sokongan pelanggan',
    subtitle: 'Hantar isu anda, terima nombor giliran, dan teruskan chat sementara admin sokongan jarak jauh menyemak kes.',
    name: 'Nama penuh',
    email: 'Emel',
    topic: 'Topik',
    details: 'Butiran isu',
    submit: 'Dapatkan nombor',
    chat: 'Hantar mesej',
    placeholder: 'Terangkan isu sokongan pelanggan dengan jelas.',
  },
};

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
  if (!auth) localStorage.removeItem(authKey);
  else localStorage.setItem(authKey, JSON.stringify(auth));
}

function formatClock(value?: string | null) {
  if (!value) return '-';
  return new Intl.DateTimeFormat('en-MY', {
    hour: '2-digit',
    minute: '2-digit',
    day: '2-digit',
    month: 'short',
  }).format(new Date(value));
}

function minutesUntil(value: string) {
  return Math.round((new Date(value).getTime() - Date.now()) / 60000);
}

function App() {
  const [view, setView] = useState<View>('public');
  const [auth, setAuthState] = useState<AuthState | null>(() => readStoredAuth());

  const setAuth = (next: AuthState | null) => {
    setAuthState(next);
    storeAuth(next);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setView('public')} title="Open customer portal">
          <span className="brand-mark">MY</span>
          <span>Agent Support</span>
        </button>
        <nav className="nav-tabs" aria-label="Main views">
          <button className={view === 'public' ? 'active' : ''} onClick={() => setView('public')}>
            <Home size={16} /> Portal
          </button>
          <button className={view === 'admin' ? 'active' : ''} onClick={() => setView('admin')}>
            <MonitorCheck size={16} /> Admin
          </button>
          <button className={view === 'docs' ? 'active' : ''} onClick={() => setView('docs')}>
            <FileText size={16} /> API
          </button>
        </nav>
        <div className="session-pill">
          <Wifi size={15} />
          {auth ? `${auth.user.name} - ${auth.user.role}` : 'Public'}
        </div>
      </header>

      {view === 'public' && <PublicPortal />}
      {view === 'admin' && (auth ? <AdminWorkspace auth={auth} setAuth={setAuth} /> : <LoginPanel setAuth={setAuth} />)}
      {view === 'docs' && <AgentDocs />}
    </div>
  );
}

function LoginPanel({ setAuth }: { setAuth: (auth: AuthState) => void }) {
  const [email, setEmail] = useState(showDemoCredentials ? 'admin@counter.local' : '');
  const [password, setPassword] = useState(showDemoCredentials ? 'admin123' : '');
  const [error, setError] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    try {
      const auth = await api.login(email, password);
      setAuth(auth);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to sign in');
    }
  }

  return (
    <main className="login-layout">
      <section className="login-panel">
        <div className="eyebrow"><ShieldCheck size={16} /> Secure WFH access</div>
        <h1>Remote support workspace</h1>
        <form onSubmit={submit} className="form-grid">
          <label>
            Email
            <input value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="primary-action" type="submit">
            <PlayCircle size={18} /> Sign in
          </button>
        </form>
        {showDemoCredentials && (
          <div className="credential-strip">
            <span>Admin: admin@counter.local / admin123</span>
            <span>Supervisor: supervisor@counter.local / super123</span>
          </div>
        )}
      </section>
    </main>
  );
}

function PublicPortal() {
  const [language, setLanguage] = useState<Language>('en');
  const [created, setCreated] = useState<Consultation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [form, setForm] = useState({
    customer_name: '',
    customer_email: '',
    topic: '',
    description: '',
  });
  const [chat, setChat] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const copy = publicCopy[language];

  const refresh = useCallback(async () => {
    if (!created) return;
    const [{ consultation }, messagePayload] = await Promise.all([
      api.getConsultation(created.id),
      api.getMessages(created.id),
    ]);
    setCreated(consultation);
    setMessages(messagePayload.messages);
  }, [created]);

  useEffect(() => {
    if (!created) return;
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, [created?.id, refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await api.createConsultation({
        ...form,
        customer_email: form.customer_email || undefined,
        language,
        source: 'public',
      });
      setCreated(result.consultation);
      const messagePayload = await api.getMessages(result.consultation.id);
      setMessages(messagePayload.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create consultation');
    } finally {
      setBusy(false);
    }
  }

  async function sendChat(event: FormEvent) {
    event.preventDefault();
    if (!created || !chat.trim()) return;
    await api.postMessage(created.id, chat, language);
    setChat('');
    refresh();
  }

  return (
    <main className="public-layout">
      <section className="portal-intake">
        <div className="lang-toggle">
          <button className={language === 'en' ? 'selected' : ''} onClick={() => setLanguage('en')}>EN</button>
          <button className={language === 'ms' ? 'selected' : ''} onClick={() => setLanguage('ms')}>BM</button>
        </div>
        <div className="eyebrow"><ClipboardList size={16} /> Remote support queue</div>
        <h1>{copy.title}</h1>
        <p>{copy.subtitle}</p>
        <form onSubmit={submit} className="form-grid">
          <label>
            {copy.name}
            <input required value={form.customer_name} onChange={(e) => setForm({ ...form, customer_name: e.target.value })} />
          </label>
          <label>
            {copy.email}
            <input type="email" value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} />
          </label>
          <label>
            {copy.topic}
            <input required value={form.topic} onChange={(e) => setForm({ ...form, topic: e.target.value })} />
          </label>
          <label>
            {copy.details}
            <textarea
              required
              rows={6}
              placeholder={copy.placeholder}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="primary-action" disabled={busy} type="submit">
            <ArrowRightLeft size={18} /> {busy ? '...' : copy.submit}
          </button>
        </form>
      </section>

      <section className="ticket-preview">
        <ConsultationRoom3D consultation={created ?? undefined} latestMessage={messages[messages.length - 1]} />
        {created ? (
          <div className="queue-card">
            <div className="queue-number">{created.queue_number}</div>
            <div className={`status-badge ${created.status}`}>{statusText[created.status]}</div>
            <div className="queue-meta">
              <span>Position {created.queue_position ?? 1}</span>
              <span>Due {formatClock(created.first_response_due_at)}</span>
            </div>
            <div className="doc-list">
              {created.document_checklist.map((doc) => (
                <span key={doc}>{doc}</span>
              ))}
            </div>
            <form onSubmit={sendChat} className="chat-compose">
              <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder={copy.placeholder} />
              <button title={copy.chat} type="submit"><Send size={18} /></button>
            </form>
          </div>
        ) : (
          <div className="queue-card muted">
            <div className="queue-number">SUP-0000</div>
            <p>Your queue number and consultation room appear here.</p>
          </div>
        )}
      </section>
    </main>
  );
}

function AdminWorkspace({ auth, setAuth }: { auth: AuthState; setAuth: (auth: AuthState | null) => void }) {
  const [queue, setQueue] = useState<QueuePayload | null>(null);
  const [selectedId, setSelectedId] = useState<string>('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');

  const refreshQueue = useCallback(async () => {
    const payload = await api.queue(auth.token);
    setQueue(payload);
    if (!selectedId) {
      const first = payload.consultations.find((item) => item.status !== 'resolved') ?? payload.consultations[0];
      if (first) setSelectedId(first.id);
    }
  }, [auth.token, selectedId]);

  useEffect(() => {
    refreshQueue().catch((err) => setError(err instanceof Error ? err.message : 'Unable to load queue'));
    const timer = window.setInterval(() => {
      refreshQueue().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshQueue]);

  const selected = useMemo(() => queue?.consultations.find((item) => item.id === selectedId), [queue, selectedId]);
  const aiByCase = useMemo(() => {
    const map = new Map<string, AiEvent>();
    for (const event of queue?.ai_events ?? []) map.set(event.consultation_id, event);
    return map;
  }, [queue]);
  const selectedAi = selected ? aiByCase.get(selected.id) : undefined;

  const refreshMessages = useCallback(async () => {
    if (!selected) return;
    const payload = await api.getMessages(selected.id);
    setMessages(payload.messages);
  }, [selected]);

  useEffect(() => {
    refreshMessages().catch(() => setMessages([]));
  }, [selected?.id, refreshMessages]);

  async function sendReply(event: FormEvent) {
    event.preventDefault();
    if (!selected || !reply.trim()) return;
    await api.postMessage(selected.id, reply, selected.language, auth.token);
    setReply('');
    await Promise.all([refreshQueue(), refreshMessages()]);
  }

  async function patch(payload: Parameters<typeof api.patchConsultation>[2]) {
    if (!selected) return;
    await api.patchConsultation(selected.id, auth.token, payload);
    await refreshQueue();
  }

  async function logout() {
    await api.logout(auth.token).catch(() => undefined);
    setAuth(null);
  }

  const groups = useMemo(() => {
    const base: Record<ConsultationStatus, Consultation[]> = {
      waiting_human: [],
      assigned: [],
      active: [],
      needs_expert_review: [],
      resolved: [],
    };
    for (const item of queue?.consultations ?? []) base[item.status].push(item);
    return base;
  }, [queue]);

  return (
    <main className="admin-layout">
      <section className="workbench">
        <div className="workspace-head">
          <div>
            <div className="eyebrow"><UserCheck size={16} /> WFH command center</div>
            <h1>Remote queue dashboard</h1>
          </div>
          <div className="admin-actions">
            <button title="Set online" onClick={() => api.setStatus(auth.token, 'online').then(refreshQueue)}>
              <Wifi size={17} /> Online
            </button>
            <button title="Sign out" onClick={logout}><LogOut size={17} /> Logout</button>
          </div>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="metrics-row">
          <Metric label="Waiting" value={queue?.metrics.waiting_human ?? 0} />
          <Metric label="Assigned" value={queue?.metrics.assigned ?? 0} />
          <Metric label="Active" value={queue?.metrics.active ?? 0} />
          <Metric label="Specialist" value={queue?.metrics.needs_expert_review ?? 0} />
        </div>
        <div className="queue-columns">
          {(['waiting_human', 'assigned', 'active', 'needs_expert_review'] as ConsultationStatus[]).map((status) => (
            <QueueColumn
              key={status}
              title={statusText[status]}
              items={groups[status]}
              selectedId={selectedId}
              onSelect={setSelectedId}
              aiByCase={aiByCase}
            />
          ))}
        </div>
        {queue?.current_user.role === 'supervisor' && <SupervisorPanel queue={queue} selected={selected} onPatch={patch} />}
      </section>

      <section className="case-panel">
        <ConsultationRoom3D consultation={selected} latestMessage={messages[messages.length - 1]} aiEvent={selectedAi} />
        {selected ? (
          <div className="case-grid">
            <section className="case-summary">
              <div className="case-title-row">
                <div>
                  <div className="queue-number compact">{selected.queue_number}</div>
                  <h2>{selected.customer_name}</h2>
                </div>
                <div className={`status-badge ${selected.status}`}>{statusText[selected.status]}</div>
              </div>
              <p>{selected.topic}</p>
              <div className="case-fields">
                <span>Priority: <strong>{selected.priority}</strong></span>
                <span>Source: <strong>{selected.source}</strong></span>
                <span>SLA: <strong className={minutesUntil(selected.first_response_due_at) < 0 ? 'danger-text' : ''}>{minutesUntil(selected.first_response_due_at)}m</strong></span>
              </div>
              <div className="doc-list">
                {selected.document_checklist.map((doc) => (
                  <span key={doc}>{doc}</span>
                ))}
              </div>
              <div className="button-row">
                <button onClick={() => patch({ needs_expert_review: true })}><AlertTriangle size={17} /> Specialist review</button>
                <button onClick={() => patch({ status: 'resolved' })}><CheckCircle2 size={17} /> Resolve</button>
              </div>
            </section>
            <section className="ai-panel">
              <div className="eyebrow"><Bot size={16} /> AI triage</div>
              <h3>{selectedAi?.classification ?? 'No AI event yet'}</h3>
              <p>{selectedAi?.summary}</p>
              <div className="confidence">
                <span style={{ width: `${Math.round((selectedAi?.confidence ?? 0) * 100)}%` }} />
              </div>
              <blockquote>{selectedAi?.suggested_reply}</blockquote>
              {selectedAi?.escalation_reason && <div className="warning">{selectedAi.escalation_reason}</div>}
            </section>
            <section className="chat-panel">
              <div className="chat-log">
                {messages.map((message) => (
                  <div className={`message ${message.role}`} key={message.id}>
                    <strong>{message.sender_name}</strong>
                    <span>{message.content}</span>
                  </div>
                ))}
              </div>
              <form className="chat-compose" onSubmit={sendReply}>
                <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply as remote admin" />
                <button title="Send reply" type="submit"><Send size={18} /></button>
              </form>
            </section>
          </div>
        ) : (
          <div className="empty-state">No consultation selected.</div>
        )}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function QueueColumn({
  title,
  items,
  selectedId,
  onSelect,
  aiByCase,
}: {
  title: string;
  items: Consultation[];
  selectedId: string;
  onSelect: (id: string) => void;
  aiByCase: Map<string, AiEvent>;
}) {
  return (
    <section className="queue-column">
      <h2>{title}</h2>
      <div className="ticket-list">
        {items.length === 0 && <div className="mini-empty">Clear</div>}
        {items.map((item) => (
          <button
            key={item.id}
            className={`ticket ${selectedId === item.id ? 'selected' : ''}`}
            onClick={() => onSelect(item.id)}
          >
            <span>{item.queue_number}</span>
            <strong>{item.customer_name}</strong>
            <small>{aiByCase.get(item.id)?.classification ?? item.topic}</small>
          </button>
        ))}
      </div>
    </section>
  );
}

function SupervisorPanel({
  queue,
  selected,
  onPatch,
}: {
  queue: QueuePayload;
  selected?: Consultation;
  onPatch: (payload: Parameters<typeof api.patchConsultation>[2]) => void;
}) {
  const admins = queue.users.filter((user) => user.role === 'admin');
  return (
    <section className="supervisor-panel">
      <div>
        <div className="eyebrow"><Gauge size={16} /> Supervisor live view</div>
        <div className="admin-roster">
          {admins.map((admin) => (
            <span key={admin.id} className={admin.status}>
              {admin.name} - {admin.language.toUpperCase()} - {admin.status}
            </span>
          ))}
        </div>
      </div>
      {selected && (
        <label>
          Reassign selected case
          <select
            value={selected.assigned_admin_id ?? ''}
            onChange={(event) => onPatch({ assigned_admin_id: event.target.value, status: 'assigned' })}
          >
            <option value="" disabled>Choose admin</option>
            {admins.map((admin) => (
              <option value={admin.id} key={admin.id}>{admin.name}</option>
            ))}
          </select>
        </label>
      )}
    </section>
  );
}

function AgentDocs() {
  const example = `curl -X POST ${API_BASE}/v1/consultations \\
  -H "Content-Type: application/json" \\
  -d '{
    "customer_name": "Agent User",
    "language": "en",
    "topic": "Login verification failed",
    "description": "The user cannot sign in after password reset.",
    "source": "agent"
  }'`;

  return (
    <main className="docs-layout">
      <section>
        <div className="eyebrow"><MessageSquareText size={16} /> Agent endpoint</div>
        <h1>REST/OpenAPI support counter</h1>
        <p>
          External AI agents can create a support ticket, add messages, request handoff, and poll status.
          MCP tools are available over Streamable HTTP at {API_BASE}/mcp/. Store the consultation ID or queue number;
          if the agent loses it, use email or name to recover the latest active support session.
        </p>
        <pre>{example}</pre>
      </section>
      <section className="endpoint-grid">
        {[
          ['MCP', '/mcp/', 'Streamable HTTP tool endpoint'],
          ['TOOL', 'find_support_consultations', 'Recover active session by email, queue number, or name'],
          ['TOOL', 'continue_support_session', 'Recover latest active session and post the next chat message'],
          ['POST', '/v1/consultations', 'Create ticket and queue number'],
          ['GET', '/v1/consultations/{id}', 'Read status and queue position'],
          ['POST', '/v1/consultations/{id}/messages', 'Append customer or agent message'],
          ['POST', '/v1/consultations/{id}/handoff', 'Escalate to human queue'],
          ['GET', '/v1/admin/queue', 'Authenticated admin queue'],
          ['PATCH', '/v1/admin/consultations/{id}', 'Authenticated status and assignment update'],
        ].map(([method, path, desc]) => (
          <div className="endpoint" key={path}>
            <strong>{method}</strong>
            <code>{path}</code>
            <span>{desc}</span>
          </div>
        ))}
      </section>
    </main>
  );
}

export default App;
