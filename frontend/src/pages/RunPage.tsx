import { FileText, FolderOpen, Hand, ListChecks, Table2, type LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getExecutions, getRun, listRunArtifacts } from '../api/client.ts';
import { useRunEvents } from '../api/useRunEvents.ts';
import type { Route } from '../routes.ts';
import ArtifactBrowser from '../run/ArtifactBrowser.tsx';
import CandidateTable from '../run/CandidateTable.tsx';
import { candidateRows } from '../run/compare.ts';
import RunChat from '../chat/RunChat.tsx';
import GatePanel from '../run/GatePanel.tsx';
import { champion } from '../report/champion.ts';
import { foldEvents, forkNote, humanize, parseGateQuestion, parseRunView, switchedToAutomatic } from '../run/model.ts';
import NodeInspector from '../run/NodeInspector.tsx';
import ReportView from '../report/ReportView.tsx';
import RunTimeline, { type Selection } from '../run/RunTimeline.tsx';
import RunHeader from '../run/RunHeader.tsx';
import { errorText } from '../ui/format.ts';

export default function RunPage({ runId, navigate }: { runId: string; navigate: (route: Route) => void }) {
  const queryClient = useQueryClient();
  const snapshot = useQuery({ queryKey: ['run', runId], queryFn: ({ signal }) => getRun(runId, signal) });
  const executions = useQuery({ queryKey: ['executions', runId], queryFn: ({ signal }) => getExecutions(runId, signal) });
  const artifacts = useQuery({ queryKey: ['artifacts', runId], queryFn: ({ signal }) => listRunArtifacts(runId, signal) });
  const [tab, setTab] = useState<'progress' | 'candidates' | 'files' | 'report'>('progress');
  const subscribedId = snapshot.data?.run.id === runId ? runId : null;
  const stream = useRunEvents(subscribedId);
  const [pane, setPane] = useState<'chat' | 'side'>('chat');
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
  const top = view === null ? null : champion(view);
  const championId = top === null ? null : top.candidateId;
  const championLabel = top === null ? null : humanize(top.label);
  const pending = data.gates
    .filter((gate) => gate.status === 'pending')
    .map((gate) => ({ gate, question: parseGateQuestion(gate.question_json) }));

  const rows = view === null ? [] : candidateRows(view);
  const tabs = [
    { id: 'progress' as const, label: 'Progress', Icon: ListChecks as LucideIcon },
    { id: 'candidates' as const, label: `Candidates (${rows.length})`, Icon: Table2 as LucideIcon },
    { id: 'files' as const, label: `Files (${artifacts.data?.length ?? 0})`, Icon: FolderOpen as LucideIcon },
    { id: 'report' as const, label: 'Report', Icon: FileText as LucideIcon },
  ];

  return (
    <div className={`split run-split ${pane === 'side' ? 'show-side' : ''}`}>
      <div className="pane-switch" role="tablist">
        <button type="button" role="tab" aria-selected={pane === 'chat'} onClick={() => setPane('chat')}>Chat</button>
        <button type="button" role="tab" aria-selected={pane === 'side'} onClick={() => setPane('side')}>
          {pending.length > 0 ? `Progress (${pending.length} waiting)` : 'Progress'}
        </button>
      </div>
      <RunChat
        runId={runId}
        feed={feed}
        view={view}
        status={data.run.status}
        mode={data.run.mode}
        forkNote={forkNote(data.run)}
        pending={pending}
        navigate={navigate}
        onStep={(step) => { setSelected(step); setTab('progress'); setPane('side'); }}
        onShowGate={() => setPane('side')}
        onTab={(next) => { setTab(next); setPane('side'); }}
      />
      <aside className="side-pane" aria-label="Run progress">
        <RunHeader
          run={data.run}
          view={view}
          costUsd={executions.data?.costUsd ?? null}
          streamStatus={stream.status}
          navigate={navigate}
          championLabel={championLabel}
          switched={switchedToAutomatic(data.gates)}
        />
        {data.execution_failed && (
          <p role="alert">The run's executor stopped with an error. Check the attempts below.</p>
        )}
        {pending.length > 0 && (
          <section aria-labelledby="gates-heading">
            <h2 id="gates-heading"><Hand className="icon" aria-hidden="true" /> Waiting for you</h2>
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
                    <item.Icon className="icon" aria-hidden="true" /> {item.label}
                  </button>
                ))}
              </div>
              <div role="tabpanel" id="run-panel" aria-labelledby={`run-tab-${tab}`}>
                {tab === 'progress' && (
                  <RunTimeline
                    view={view}
                    executions={executions.data?.executions ?? []}
                    pending={pending.flatMap(({ question }) => (question === null ? [] : [question]))}
                    selected={selected}
                    championId={championId}
                    onSelect={setSelected}
                    detail={selected === null
                      ? null
                      : <NodeInspector view={view} data={executions.data} selected={selected} artifacts={artifacts.data} />}
                  />
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
      </aside>
    </div>
  );
}
