import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getExecutions, getRun, listRunArtifacts } from '../api/client.ts';
import { useRunEvents } from '../api/useRunEvents.ts';
import type { Route } from '../routes.ts';
import ArtifactBrowser from '../run/ArtifactBrowser.tsx';
import CandidateTable from '../run/CandidateTable.tsx';
import { candidateRows } from '../run/compare.ts';
import ChatPanel from '../run/ChatPanel.tsx';
import GatePanel from '../run/GatePanel.tsx';
import { foldEvents, parseGateQuestion, parseRunView } from '../run/model.ts';
import NodeInspector from '../run/NodeInspector.tsx';
import ReportView from '../report/ReportView.tsx';
import RunGraph, { type Selection } from '../run/RunGraph.tsx';
import RunHeader from '../run/RunHeader.tsx';
import { errorText } from '../ui/format.ts';

export default function RunPage({ runId, navigate }: { runId: string; navigate: (route: Route) => void }) {
  const queryClient = useQueryClient();
  const snapshot = useQuery({ queryKey: ['run', runId], queryFn: ({ signal }) => getRun(runId, signal) });
  const executions = useQuery({ queryKey: ['executions', runId], queryFn: ({ signal }) => getExecutions(runId, signal) });
  const artifacts = useQuery({ queryKey: ['artifacts', runId], queryFn: ({ signal }) => listRunArtifacts(runId, signal) });
  const [tab, setTab] = useState<'steps' | 'candidates' | 'files' | 'report'>('steps');
  const subscribedId = snapshot.data?.run.id === runId ? runId : null;
  const stream = useRunEvents(subscribedId);
  const [selected, setSelected] = useState<Selection | null>(null);
  const feed = useMemo(() => foldEvents(stream.events), [stream.events]);
  const lastType = stream.lastEvent?.type ?? '';

  useEffect(() => {
    if (!/^(node|gate|run)\./.test(lastType)) return undefined;
    const timer = setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: ['run', runId] });
      void queryClient.invalidateQueries({ queryKey: ['executions', runId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', runId] });
    }, 300);
    return () => clearTimeout(timer);
  }, [stream.lastSeq, runId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (snapshot.isPending) return <p>Loading run…</p>;
  if (snapshot.isError) return <p role="alert">{errorText(snapshot.error)}</p>;
  const data = snapshot.data;
  if (data.run.id !== runId) return <p>Loading run…</p>;

  const view = parseRunView(data.state);
  const pending = data.gates
    .filter((gate) => gate.status === 'pending')
    .map((gate) => ({ gate, question: parseGateQuestion(gate.question_json) }));

  const rows = view === null ? [] : candidateRows(view);
  const tabs = [
    { id: 'steps' as const, label: 'Steps' },
    { id: 'candidates' as const, label: `Candidates (${rows.length})` },
    { id: 'files' as const, label: `Files (${artifacts.data?.length ?? 0})` },
    { id: 'report' as const, label: 'Report' },
  ];

  return (
    <section aria-label="Run workspace">
      <RunHeader
        run={data.run}
        view={view}
        costUsd={executions.data?.costUsd ?? null}
        streamStatus={stream.status}
        navigate={navigate}
      />
      {pending.length > 0 && (
        <section aria-labelledby="gates-heading">
          <h2 id="gates-heading">Decisions waiting for you</h2>
          {pending.map(({ gate, question }) =>
            question === null
              ? (
                <div className="panel gate" key={gate.id}>
                  <p role="alert">This decision cannot be displayed.</p>
                  <pre className="json-view">{gate.question_json}</pre>
                </div>
              )
              : (
                <GatePanel
                  key={`${gate.id}:${question.revision}`}
                  runId={runId}
                  gate={gate}
                  question={question}
                  skippable={view?.spine.find((node) => node.name === question.node)?.skippable ?? false}
                  spine={view?.spine ?? []}
                  navigate={navigate}
                />
              ))}
        </section>
      )}
      {data.execution_failed && (
        <p role="alert">The run's executor stopped with an error. Check the attempts below.</p>
      )}
      <div className="workspace">
        <div>
          {view === null
            ? <p role="alert">This run's state cannot be displayed.</p>
            : (
              <>
                <div className="tabs" role="tablist">
                  {tabs.map((item) => (
                    <button
                      key={item.id} type="button" role="tab" id={`run-tab-${item.id}`} aria-selected={tab === item.id}
                      aria-controls="run-panel" onClick={() => setTab(item.id)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <div role="tabpanel" id="run-panel" aria-labelledby={`run-tab-${tab}`}>
                  {tab === 'steps' && (
                    <>
                      <RunGraph
                        view={view}
                        executions={executions.data?.executions ?? []}
                        pending={pending.flatMap(({ question }) => (question === null ? [] : [question]))}
                        selected={selected}
                        onSelect={setSelected}
                      />
                      <NodeInspector view={view} data={executions.data} selected={selected} artifacts={artifacts.data} />
                    </>
                  )}
                  {tab === 'candidates' && <CandidateTable rows={rows} />}
                  {tab === 'files' && (
                    artifacts.isPending
                      ? <p>Loading files…</p>
                      : artifacts.isError
                        ? <p role="alert">{errorText(artifacts.error)}</p>
                        : <ArtifactBrowser entries={artifacts.data} />
                  )}
                  {tab === 'report' && <ReportView run={data.run} view={view} />}
                </div>
              </>
            )}
        </div>
        <ChatPanel runId={runId} feed={feed} navigate={navigate} />
      </div>
    </section>
  );
}
