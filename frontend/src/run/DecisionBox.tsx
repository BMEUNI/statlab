import { FastForward, Hand } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { answerGate } from '../api/client.ts';
import type { GateAction, GateQuestion, GateSummary, RunMode } from '../api/types.ts';
import type { Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import GatePanel from './GatePanel.tsx';
import {
  decisionLead,
  gateNotes,
  groupDecisions,
  humanize,
  nodeLabel,
  PHASE_LABELS,
  type DecisionItem,
  type RunView,
} from './model.ts';

type Answer = { gateId: string; revision: number; action: GateAction };
type Props = {
  runId: string;
  mode: RunMode;
  pending: { gate: GateSummary; question: GateQuestion | null }[];
  view: RunView | null;
  navigate: (route: Route) => void;
  openGate: string | null;
  onOpenGate: (gateId: string | null) => void;
};

export default function DecisionBox({ runId, mode, pending, view, navigate, openGate, onOpenGate }: Props) {
  const queryClient = useQueryClient();
  const items: DecisionItem[] = pending.flatMap(({ gate, question }) => (question === null ? [] : [{ gate, question }]));
  const broken = pending.filter((entry) => entry.question === null);
  const groups = groupDecisions(items);
  const ordered = groups.flatMap((group) => group.items);
  const canApprove = (item: DecisionItem) => item.question.options.includes('approve');
  const answerOf = (item: DecisionItem, action: GateAction): Answer => ({
    gateId: item.gate.id,
    revision: item.question.revision,
    action,
  });
  const auto = ordered.find((item) => item.question.options.includes('approve_all_remaining'));
  const answer = useMutation<void, Error, Answer[]>({
    mutationFn: async (list) => {
      for (const { gateId, revision, action } of list) {
        await answerGate(runId, gateId, { revision, action });
      }
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
        queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
      ]);
    },
  });
  const busy = answer.isPending;

  const label = (item: DecisionItem): string => {
    const id = item.question.candidateId ?? '';
    const branch = view?.branches.find((entry) => entry.id === id);
    return branch === undefined ? id : humanize(branch.label);
  };
  const toggle = (gateId: string) => onOpenGate(openGate === gateId ? null : gateId);
  const panel = (item: DecisionItem) => (
    <GatePanel
      key={`${item.gate.id}:${item.question.revision}`}
      runId={runId}
      gate={item.gate}
      question={item.question}
      skippable={view?.spine.find((node) => node.name === item.question.node)?.skippable ?? false}
      spine={view?.spine ?? []}
      navigate={navigate}
    />
  );
  const notesList = (notes: string[]) => (
    <ul>{notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
  );

  return (
    <section className="decision-box" aria-labelledby="decision-heading">
      <h2 id="decision-heading">
        <Hand className="icon" aria-hidden="true" />
        {`Waiting for you (${items.length + broken.length})`}
      </h2>
      {broken.map(({ gate }) => (
        <div key={gate.id}>
          <p role="alert">A decision cannot be displayed.</p>
          <pre className="json-view">{gate.question_json}</pre>
        </div>
      ))}
      {groups.map((group) => {
        const [first] = group.items;
        if (first === undefined) return null;
        const n = group.items.length;
        const suffix = n > 1 ? ` · ${n} models` : group.candidate ? ` · ${label(first)}` : '';
        return (
          <article className="decision-group" key={group.key}>
            <div className="decision-head">
              <h3>{`${nodeLabel(group.node)}${suffix}`}</h3>
              <span className="pill gate">{PHASE_LABELS[group.phase]}</span>
            </div>
            <p className="decision-lead">{decisionLead(group)}</p>
            {group.notes !== null && group.notes.length > 0 && (
              <details className="decision-notes">
                <summary>{`Points to check (${group.notes.length})${n > 1 ? ', the same for every model' : ''}`}</summary>
                {notesList(group.notes)}
              </details>
            )}
            {n > 1 && (
              <ul className="decision-rows">
                {group.items.map((item) => {
                  const itemNotes = gateNotes(item.question);
                  return (
                    <li key={item.gate.id}>
                      <span className="decision-model">{label(item)}</span>
                      {group.notes === null && itemNotes.length > 0 && (
                        <details className="decision-notes">
                          <summary>{`Points to check (${itemNotes.length})`}</summary>
                          {notesList(itemNotes)}
                        </details>
                      )}
                      <span className="decision-row-actions">
                        {canApprove(item) && (
                          <button type="button" disabled={busy} onClick={() => answer.mutate([answerOf(item, 'approve')])}>
                            Approve
                          </button>
                        )}
                        <button
                          type="button"
                          className="link-button"
                          disabled={busy}
                          aria-expanded={openGate === item.gate.id}
                          onClick={() => toggle(item.gate.id)}
                        >
                          Review or change
                        </button>
                      </span>
                      {openGate === item.gate.id && panel(item)}
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="decision-actions">
              {group.items.every(canApprove) && (
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => answer.mutate(group.items.map((item) => answerOf(item, 'approve')))}
                >
                  {n > 1 ? `Approve all ${n}` : 'Approve'}
                </button>
              )}
              {n === 1 && (
                <button
                  type="button"
                  className="link-button"
                  disabled={busy}
                  aria-expanded={openGate === first.gate.id}
                  onClick={() => toggle(first.gate.id)}
                >
                  Review or change
                </button>
              )}
            </div>
            {n === 1 && openGate === first.gate.id && panel(first)}
          </article>
        );
      })}
      {mode === 'guided' && broken.length === 0 && items.every(canApprove) && auto !== undefined && (
        <div className="decision-footer">
          <button
            type="button"
            disabled={busy}
            onClick={() => answer.mutate([
              answerOf(auto, 'approve_all_remaining'),
              ...ordered.filter((item) => item !== auto).map((item) => answerOf(item, 'approve')),
            ])}
          >
            <FastForward className="icon" aria-hidden="true" />Approve all and finish automatically
          </button>
        </div>
      )}
      {answer.isError && <p role="alert">{errorText(answer.error)}</p>}
    </section>
  );
}
