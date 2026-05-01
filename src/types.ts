export type Language = 'en' | 'ms';
export type Role = 'admin' | 'supervisor';
export type AdminStatus = 'online' | 'away' | 'offline';
export type ConsultationStatus =
  | 'waiting_human'
  | 'assigned'
  | 'active'
  | 'needs_expert_review'
  | 'resolved';

export interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: Role;
  language: Language;
  status: AdminStatus;
  last_seen?: string;
}

export interface Consultation {
  id: string;
  queue_number: string;
  source: 'public' | 'agent';
  customer_name: string;
  customer_email?: string;
  language: Language;
  topic: string;
  description: string;
  priority: 'low' | 'medium' | 'high';
  status: ConsultationStatus;
  assigned_admin_id?: string | null;
  needs_expert_review: number | boolean;
  document_checklist: string[];
  created_at: string;
  updated_at: string;
  first_response_due_at: string;
  resolved_at?: string | null;
  queue_position?: number;
}

export interface Message {
  id: string;
  consultation_id: string;
  role: 'customer' | 'agent' | 'ai' | 'admin' | 'system';
  sender_name: string;
  content: string;
  language: Language;
  created_at: string;
}

export interface AiEvent {
  id: string;
  consultation_id: string;
  classification: string;
  summary: string;
  confidence: number;
  suggested_reply: string;
  escalation_reason?: string | null;
  created_at: string;
}

export interface QueuePayload {
  current_user: AdminUser;
  consultations: Consultation[];
  ai_events: AiEvent[];
  users: AdminUser[];
  metrics: Record<string, number>;
}

export interface AuthState {
  token: string;
  expires_at: string;
  user: AdminUser;
}
