import { useEffect, useState } from 'react';
import { connectRunEvents, type StreamStatus } from './events.ts';
import type { RunEvent } from './types.ts';

type EventState = { runId: string | null; status: StreamStatus; lastSeq: number; lastEvent: RunEvent | null };
type RunEvents = { status: StreamStatus; lastSeq: number; lastEvent: RunEvent | null };

export function useRunEvents(runId: string | null): RunEvents {
  const [state, setState] = useState<EventState>({ runId: null, status: 'closed', lastSeq: 0, lastEvent: null });
  useEffect(() => {
    if (runId === null) {
      setState({ runId: null, status: 'closed', lastSeq: 0, lastEvent: null });
      return undefined;
    }
    let alive = true;
    setState({ runId, status: 'connecting', lastSeq: 0, lastEvent: null });
    const cleanup = connectRunEvents(
      runId,
      (event) => {
        if (alive) setState((current) => ({ ...current, runId, lastSeq: event.seq, lastEvent: event }));
      },
      (status) => {
        if (alive) setState((current) => ({ ...current, runId, status }));
      },
    );
    return () => {
      alive = false;
      cleanup();
    };
  }, [runId]);
  if (state.runId !== runId) {
    return runId === null
      ? { status: 'closed', lastSeq: 0, lastEvent: null }
      : { status: 'connecting', lastSeq: 0, lastEvent: null };
  }
  return { status: state.status, lastSeq: state.lastSeq, lastEvent: state.lastEvent };
}
