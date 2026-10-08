import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { answerGate, ApiError, checkGateEdits } from '../api/client.ts';
import type { ControlReply, GateSubmission, GateSummary, GateQuestion, JsonObject } from '../api/types.ts';
import type { Route } from '../routes.ts';
import SchemaForm from '../setup/SchemaForm.tsx';
import { errorText } from '../ui/format.ts';
import DecisionView from './DecisionView.tsx';
import {
  availableActions,
  EMPTY_VOCABULARY,
  splitLead,
  gateEdits,
  gateFieldSplit,
  nodeLabel,
  PHASE_LABELS,
  type SpineNode,
} from './model.ts';

type Props = {
  runId: string;
  gate: GateSummary;
  question: GateQuestion;
  skippable: boolean;
  spine: SpineNode[];
  navigate: (route: Route) => void;
};

const STOP_CONFIRM = 'Stop this run? It cannot be resumed.';
const REWIND_CONFIRM = 'Start a new run from this step? This run will be replaced.';

export default function GatePanel({ runId, gate, question, skippable, spine, navigate }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JsonObject>(question.proposal);
  const [instruction, setInstruction] = useState('');
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState(spine[0]?.name ?? '');
  const [issues, setIssues] = useState<JsonObject[]>([]);
  const [reply, setReply] = useState<string | null>(null);
  const actions = availableActions(question, skippable);
  const edits = gateEdits(question.proposal, draft, question.editable);
  const hasEdits = Object.keys(edits).length > 0;
  const editable = actions.includes('approve_with_edits');
  const { main, rest } = gateFieldSplit(question);
  const reasoning = splitLead(question.reasoning, 3);

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['run', runId] }),
      queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
    ]);
  const mutation = useMutation<ControlReply, Error, GateSubmission>({
    mutationFn: async (submission) => {
      if (submission.action === 'approve_with_edits' && submission.edits !== undefined) {
        const list = await checkGateEdits(runId, gate.id, submission.edits);
        if (list.length > 0) {
          setIssues(list);
          return { text: '', category: 'noop', newRunId: null };
        }
        setIssues([]);
      }
      return answerGate(runId, gate.id, submission);
    },
    onSuccess: async (result) => {
      if (result.category === 'noop') return;
      setReply(result.text);
      await refresh();
      if (result.newRunId !== null) navigate({ name: 'run', runId: result.newRunId });
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) await queryClient.invalidateQueries({ queryKey: ['run', runId] });
    },
  });
  const busy = mutation.isPending;
  const send = (submission: Omit<GateSubmission, 'revision'>) => mutation.mutate({ revision: question.revision, ...submission });
  const trimmedInstruction = instruction.trim();
  const trimmedReason = reason.trim();
  const rewindSelect = (id: string) => (
    <div className="field">
      <label htmlFor={id}>Start again from</label>
      <select id={id} value={target} onChange={(e) => setTarget(e.target.value)}>
        {spine.map((node) => <option key={node.name} value={node.name}>{nodeLabel(node.name)}</option>)}
      </select>
    </div>
  );

  return (
    <article className="panel gate" aria-labelledby={`gate-${gate.id}`}>
      <h3 id={`gate-${gate.id}`}>
        {`Your decision: ${nodeLabel(question.node)}`}{question.candidateId === null ? '' : ` for ${question.candidateId}`}{' '}
        <span className="pill gate">{PHASE_LABELS[question.phase]}</span>
      </h3>
      <p className="gate-lead">
        {question.phase === 'before'
          ? "I'm about to run this step. Here is my plan."
          : question.phase === 'after'
            ? 'This step has finished. Please check the result.'
            : `This step failed: ${question.failure?.message ?? 'unknown error'}`}
      </p>
      {question.failure !== null && <p role="alert">{question.failure.message}</p>}
      {question.reasoning.trim() !== '' && (
        <>
          <p>{reasoning.lead}</p>
          {reasoning.more !== null && (
            <details>
              <summary>Read more</summary>
              <p className="reasoning">{reasoning.more}</p>
            </details>
          )}
        </>
      )}
      {editable
        ? (
          <>
            <fieldset className="gate-main">
              <legend>Main settings</legend>
              {main.length > 0
                ? (
                  <SchemaForm
                    schema={question.decisionSchema} defName={null} path={[]} draft={draft} onDraft={setDraft}
                    columns={[]} vocabulary={EMPTY_VOCABULARY} only={main}
                  />
                )
                : <p className="help">The agent kept every setting at its default.</p>}
            </fieldset>
            {rest.length > 0 && (
              <details className="all-settings">
                <summary>{`All settings (${rest.length} more)`}</summary>
                <SchemaForm
                  schema={question.decisionSchema} defName={null} path={[]} draft={draft} onDraft={setDraft}
                  columns={[]} vocabulary={EMPTY_VOCABULARY} only={rest}
                />
              </details>
            )}
          </>
        )
        : (
          <fieldset>
            <legend>Proposal</legend>
            <DecisionView value={question.proposal} />
          </fieldset>
        )}
      {issues.length > 0 && (
        <div role="alert">
          <p>Fix these before approving:</p>
          <ul>
            {issues.map((issue, index) => (
              <li key={index}><strong>{String(issue.field)}</strong> {String(issue.message)}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="actions">
        {actions.includes('approve') && !hasEdits && (
          <button type="button" className="primary" disabled={busy} onClick={() => send({ action: 'approve' })}>Approve</button>
        )}
        {hasEdits && (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => send({ action: 'approve_with_edits', edits })}>
              Approve with my edits
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(question.proposal);
                setIssues([]);
              }}
            >Discard my edits</button>
          </>
        )}
        {actions.includes('approve_all_remaining') && (
          <button type="button" disabled={busy || hasEdits} onClick={() => send({ action: 'approve_all_remaining' })}>
            Approve and run the rest automatically
          </button>
        )}
      </div>
      {hasEdits && actions.includes('approve_all_remaining') && <p className="help">Approve or discard your edits first.</p>}
      <details className="gate-more">
        <summary>Other options</summary>
      {question.context.length > 0 && (
        <details>
          <summary>Context</summary>
          <ul>{question.context.map((line, index) => <li key={index}>{line}</li>)}</ul>
        </details>
      )}
      {question.evidence.length > 0 && (
        <details>
          <summary>{`Evidence the agent used (${question.evidence.length})`}</summary>
          <pre className="json-view">{JSON.stringify(question.evidence, null, 2)}</pre>
        </details>
      )}
      {question.history.length > 0 && (
        <details>
          <summary>{`Earlier proposals (${question.history.length})`}</summary>
          <pre className="json-view">{JSON.stringify(question.history, null, 2)}</pre>
        </details>
      )}
      {actions.includes('instruct') && (
        <details>
          <summary>Ask for a revision</summary>
          <div className="field">
            <label htmlFor={`instruct-${gate.id}`}>Tell the agent what to change</label>
            <textarea id={`instruct-${gate.id}`} rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} />
          </div>
          <button
            type="button"
            disabled={busy || trimmedInstruction === ''}
            onClick={() => send({ action: 'instruct', instruction: trimmedInstruction })}
          >Ask for a revision</button>
        </details>
      )}
      {actions.includes('skip_node') && (
        <details>
          <summary>Skip this step</summary>
          <div className="field">
            <label htmlFor={`skip-${gate.id}`}>Why skip this step?</label>
            <textarea id={`skip-${gate.id}`} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <button
            type="button"
            disabled={busy || trimmedReason === ''}
            onClick={() => send({ action: 'skip_node', reason: trimmedReason })}
          >Skip this step</button>
        </details>
      )}
      {actions.includes('rewind') && (
        <details>
          <summary>Rewind to an earlier step</summary>
          {rewindSelect(`rewind-${gate.id}`)}
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (window.confirm(REWIND_CONFIRM)) send({ action: 'rewind', target });
            }}
          >Rewind</button>
        </details>
      )}
      {actions.includes('abort') && (
        <div className="actions">
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(STOP_CONFIRM)) send({ action: 'abort' });
            }}
          >Stop the run</button>
        </div>
      )}
      </details>
      {mutation.isError && (
        <div role="alert">
          <p>{errorText(mutation.error)}</p>
          {mutation.error instanceof ApiError && mutation.error.status === 409 && (
            <p>This decision changed or was already answered. The page now shows its latest state.</p>
          )}
        </div>
      )}
      {reply !== null && reply !== '' && <p className="ok">{reply}</p>}
    </article>
  );
}
