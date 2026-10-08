import { useQuery } from '@tanstack/react-query';
import { getRun } from '../api/client.ts';
import { useRunEvents } from '../api/useRunEvents.ts';
import { errorText } from '../ui/format.ts';

export default function RunPage({ runId }: { runId: string }) {
  const snapshot = useQuery({ queryKey: ['run', runId], queryFn: ({ signal }) => getRun(runId, signal) });
  const subscribedId = snapshot.data?.run.id === runId ? runId : null;
  const stream = useRunEvents(subscribedId);
  return (
    <section aria-labelledby="run-heading">
      <h1 id="run-heading">Run</h1>
      {snapshot.isPending && <p>Loading run…</p>}
      {snapshot.isError && <p role="alert">{errorText(snapshot.error)}</p>}
      {snapshot.isSuccess && snapshot.data.run.id === runId && (
        <>
          <p className="secondary-line">Snapshot captured when loaded.</p>
          <dl className="facts">
            <dt>Run</dt><dd>{snapshot.data.run.id}</dd>
            <dt>Mode</dt><dd>{snapshot.data.run.mode}</dd>
            <dt>Status</dt><dd>{snapshot.data.run.status}</dd>
            <dt>Current node</dt><dd>{snapshot.data.run.current_node ?? 'None'}</dd>
            <dt>Pending gates</dt><dd>{snapshot.data.gates.filter((gate) => gate.status === 'pending').length}</dd>
            <dt>Execution failed</dt><dd>{snapshot.data.execution_failed ? 'Yes' : 'No'}</dd>
          </dl>
          <section aria-label="Run event stream" aria-live="polite" className="panel">
            <p>Stream: {stream.status}</p>
            <p>Last sequence: {stream.lastSeq}</p>
            <p>Last event: {stream.lastEvent?.type ?? 'No events yet.'}</p>
          </section>
        </>
      )}
    </section>
  );
}
