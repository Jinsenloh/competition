import type { AuthState, Consultation, Language, Message, QueuePayload } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://127.0.0.1:8787' : window.location.origin);

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `Request failed with ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Keep default error text.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  login(email: string, password: string) {
    return request<AuthState>('/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
  },
  logout(token: string) {
    return request<void>('/v1/auth/logout', { method: 'POST' }, token);
  },
  createConsultation(payload: {
    customer_name: string;
    customer_email?: string;
    language: Language;
    topic: string;
    description: string;
    source: 'public' | 'agent';
  }) {
    return request<{ consultation: Consultation; ai_event: unknown }>('/v1/consultations', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  getConsultation(id: string) {
    return request<{ consultation: Consultation; ai_event: unknown }>(`/v1/consultations/${id}`);
  },
  getMessages(id: string) {
    return request<{ messages: Message[] }>(`/v1/consultations/${id}/messages`);
  },
  postMessage(id: string, content: string, language: Language, token?: string, role: 'customer' | 'agent' = 'customer') {
    return request<{ message: Message }>(
      `/v1/consultations/${id}/messages`,
      {
        method: 'POST',
        body: JSON.stringify({ content, language, role }),
      },
      token,
    );
  },
  handoff(id: string, reason: string) {
    return request<{ consultation: Consultation }>(`/v1/consultations/${id}/handoff`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },
  queue(token: string) {
    return request<QueuePayload>('/v1/admin/queue', {}, token);
  },
  patchConsultation(
    id: string,
    token: string,
    payload: Partial<{
      status: Consultation['status'];
      priority: Consultation['priority'];
      assigned_admin_id: string | null;
      needs_expert_review: boolean;
    }>,
  ) {
    return request<{ consultation: Consultation }>(
      `/v1/admin/consultations/${id}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      token,
    );
  },
  setStatus(token: string, status: 'online' | 'away' | 'offline') {
    return request<{ user: AuthState['user'] }>(
      '/v1/admin/me/status',
      {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      },
      token,
    );
  },
};

export { API_BASE };
