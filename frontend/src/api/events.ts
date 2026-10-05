import { ApiError, isJsonObject } from './client.ts';
import type { RunEvent } from './types.ts';

export type StreamStatus = 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'error';
export type EventSourceHandle = {
  readonly readyState: number;
  close(): void;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
};

function invalidEvent(): ApiError {
  return new ApiError('Backend returned an invalid event.');
}

export function parseRunEvent(value: unknown, runId: string, lastEventId: string): RunEvent {
  if (!isJsonObject(value)) throw invalidEvent();
  const seq = value.seq;
  const eventRunId = value.run_id;
  const ts = value.ts;
  const type = value.type;
  const payload = value.payload;
  if (
    typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 1 ||
    typeof eventRunId !== 'string' || eventRunId !== runId ||
    typeof ts !== 'string' || typeof type !== 'string' || !isJsonObject(payload) ||
    lastEventId !== String(seq) || !/^[1-9][0-9]*$/.test(lastEventId)
  ) throw invalidEvent();
  return { seq, run_id: eventRunId, ts, type, payload };
}

export function connectRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onStatus: (status: StreamStatus) => void,
  createSource: (url: string) => EventSourceHandle = (url) => new EventSource(url),
): () => void {
  onStatus('connecting');
  let lastSeq = 0;
  let active = true;
  let source: EventSourceHandle | null = null;
  let closed = false;
  const close = (): void => {
    if (!closed && source !== null) {
      closed = true;
      source.onopen = null;
      source.onerror = null;
      source.onmessage = null;
      source.close();
    }
  };
  if (runId.trim() === '') {
    active = false;
    onStatus('error');
    return () => undefined;
  }
  try {
    source = createSource(`/api/runs/${encodeURIComponent(runId)}/events?since=0`);
  } catch {
    active = false;
    onStatus('error');
    return () => undefined;
  }
  source.onopen = () => {
    if (active) onStatus('connected');
  };
  source.onerror = () => {
    if (active) onStatus(source?.readyState === 2 ? 'closed' : 'reconnecting');
  };
  source.onmessage = (message) => {
    if (!active) return;
    let event: RunEvent;
    try {
      event = parseRunEvent(JSON.parse(message.data) as unknown, runId, message.lastEventId);
    } catch {
      active = false;
      close();
      onStatus('error');
      return;
    }
    if (event.seq <= lastSeq) return;
    if (event.seq !== lastSeq + 1) {
      active = false;
      close();
      onStatus('error');
      return;
    }
    lastSeq = event.seq;
    onEvent(event);
  };
  return () => {
    if (!active) return;
    active = false;
    close();
    onStatus('closed');
  };
}
