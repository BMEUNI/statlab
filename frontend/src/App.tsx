import { useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError, getRun, listProjects } from './api/client.ts';
import { useRunEvents } from './api/useRunEvents.ts';

function errorMessage(error: Error): string {
  return error instanceof ApiError ? error.message : 'Request failed.';
}

export default function App() {
  const [draft, setDraft] = useState('');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const projects = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => listProjects(signal) });
  const snapshot = useQuery({
    queryKey: ['run', activeId],
    enabled: activeId !== null,
    queryFn: ({ signal }) => activeId === null ? Promise.reject(new Error('Run ID is required.')) : getRun(activeId, signal),
  });
  const subscribedId = activeId !== null && snapshot.data?.run.id === activeId ? activeId : null;
  const stream = useRunEvents(subscribedId);

  function inspect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draft.trim();
    if (value === '') {
      setInputError('Enter a run ID.');
      setActiveId(null);
      return;
    }
    setInputError(null);
    setActiveId(value);
  }

  return (
    <main className="shell">
      <header>
        <p className="eyebrow">Local modelling workspace</p>
        <h1>Statlab</h1>
      </header>
      <section aria-labelledby="projects-heading" className="card">
        <h2 id="projects-heading">Projects</h2>
        {projects.isPending && <p>Loading projects…</p>}
        {projects.isError && <p role="alert">{errorMessage(projects.error)}</p>}
        {projects.isSuccess && projects.data.length === 0 && <p>No projects yet.</p>}
        {projects.isSuccess && projects.data.length > 0 && (
          <ul className="project-list">
            {projects.data.map((project) => (
              <li key={project.id}><strong>{project.name}</strong><span>{project.slug}</span></li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="run-heading" className="card">
        <h2 id="run-heading">Inspect a run</h2>
        <form onSubmit={inspect}>
          <label htmlFor="run-id">Run ID</label>
          <div className="form-row">
            <input id="run-id" value={draft} onChange={(event) => setDraft(event.target.value)} />
            <button type="submit">Inspect run</button>
          </div>
        </form>
        {inputError !== null && <p role="alert">{inputError}</p>}
        {activeId !== null && snapshot.isPending && <p>Loading run…</p>}
        {activeId !== null && snapshot.isError && <p role="alert">{errorMessage(snapshot.error)}</p>}
        {snapshot.isSuccess && snapshot.data.run.id === activeId && (
          <>
            <p className="note">Snapshot captured when loaded.</p>
            <dl className="facts">
              <dt>Run</dt><dd>{snapshot.data.run.id}</dd>
              <dt>Mode</dt><dd>{snapshot.data.run.mode}</dd>
              <dt>Status</dt><dd>{snapshot.data.run.status}</dd>
              <dt>Current node</dt><dd>{snapshot.data.run.current_node ?? 'None'}</dd>
              <dt>Pending gates</dt><dd>{snapshot.data.gates.filter((gate) => gate.status === 'pending').length}</dd>
              <dt>Execution failed</dt><dd>{snapshot.data.execution_failed ? 'Yes' : 'No'}</dd>
            </dl>
            <section aria-label="Run event stream" aria-live="polite" className="stream-status">
              <p>Stream: {stream.status}</p>
              <p>Last sequence: {stream.lastSeq}</p>
              <p>Last event: {stream.lastEvent?.type ?? 'No events yet.'}</p>
            </section>
          </>
        )}
      </section>
    </main>
  );
}
