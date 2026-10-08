import { parseSetupState } from '../api/client.ts';
import type { ChatTurn, SetupState } from '../api/types.ts';

export type ChatMessage = { id: string; role: 'user' | 'assistant' | 'note'; text: string; tone?: 'fault'; link?: { href: string; label: string } };
export type SetupSession = { messages: ChatMessage[]; state: SetupState };
export type SetupStep = 'project' | 'dataset' | 'target' | 'contract' | 'mode' | 'start';

export const EMPTY_STATE: SetupState = { project_id: null, dataset_id: null, target: null, contract_id: null, mode: null };
export const GREETING = "Hi! I'll help you set up a modelling run. Which project are you working on? Pick one below, or tell me a name for a new one.";

export function newSession(): SetupSession {
  return { messages: [{ id: 'greeting', role: 'assistant', text: GREETING }], state: EMPTY_STATE };
}

export function nextStep(state: SetupState): SetupStep {
  if (state.project_id === null) return 'project';
  if (state.dataset_id === null) return 'dataset';
  if (state.target === null) return 'target';
  if (state.contract_id === null) return 'contract';
  if (state.mode === null) return 'mode';
  return 'start';
}

export function toTurns(messages: ChatMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const message of messages) {
    if (message.role !== 'note') turns.push({ role: message.role, text: message.text });
  }
  return turns.slice(-40);
}

export const CURRENT_KEY = 'statlab.setup.v1';
export function runChatKey(runId: string): string {
  return 'statlab.chat.v1.' + runId;
}

export type KeyValueStore = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export function browserStore(): KeyValueStore | null {
  try {
    return (globalThis as { localStorage?: KeyValueStore }).localStorage ?? null;
  } catch {
    return null;
  }
}

function isMessage(value: unknown): value is ChatMessage {
  if (value === null || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === 'string' && typeof item.text === 'string' &&
    (item.role === 'user' || item.role === 'assistant' || item.role === 'note') &&
    (item.tone === undefined || item.tone === 'fault');
}

export function loadSession(store: KeyValueStore | null, key: string): SetupSession | null {
  if (store === null) return null;
  try {
    const raw = store.getItem(key);
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (value === null || typeof value !== 'object') return null;
    const { messages, state } = value as { messages?: unknown; state?: unknown };
    if (!Array.isArray(messages) || !messages.every(isMessage)) return null;
    return { messages, state: parseSetupState(state) };
  } catch {
    return null;
  }
}

export function saveSession(store: KeyValueStore | null, key: string, session: SetupSession): void {
  if (store === null) return;
  try {
    store.setItem(key, JSON.stringify(session));
  } catch {
    // storage unavailable: the chat simply is not persisted
  }
}

export function clearSession(store: KeyValueStore | null, key: string): void {
  if (store === null) return;
  try {
    store.removeItem(key);
  } catch {
    // storage unavailable
  }
}

export function newId(): string {
  return crypto.randomUUID();
}
