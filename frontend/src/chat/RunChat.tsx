import { useState } from 'react';
import { FastForward, SlidersHorizontal, ThumbsUp } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, answerGate, sendMessage } from '../api/client.ts';
import type { ControlReply, GateAction, GateQuestion, JsonValue, GateSummary, RunMode, RunStatus } from '../api/types.ts';
import { STATUS_LABELS, firstSentences, gateFieldSplit, humanize, nodeLabel, type RunFeed, type RunView } from '../run/model.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import Composer from './Composer.tsx';
import MessageList from './MessageList.tsx';
import RunSummary from './RunSummary.tsx';
import { browserStore, loadSession, runChatKey, type ChatMessage } from './session.ts';

type Step = { node: string; candidateId: string | null };
type Pending = { gate: GateSummary; question: GateQuestion | null };
type Props = {
  runId: string;
  feed: RunFeed;
  view: RunView | null;
  status: RunStatus;
  mode: RunMode;
  forkNote: string | null;
  pending: Pending[];
  navigate: (route: Route) => void;
  onStep: (step: Step) => void;
  onShowGate: () => void;
  onTab: (tab: 'candidates' | 'report') => void;
};

const INTRO = `I'll post a short update here after each step, and a summary of the results at the end.

You can write to me at any time, also while I wait for your decision:
- ask why I did something ("why did you drop LoanID?")
- change a setting ("use 10 folds", "set the test size to 0.3")
- redo part of the run ("go back to data profiling")
- control the run ("pause", "resume", "stop")`;
const GUIDED =
  '\n\nIn guided mode I stop and ask you before each key decision. Answer with the buttons or in your own words.';

function valueText(value: JsonValue | undefined): string {
  if (value === undefined || value === null) return 'not set';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.length === 0 ? 'none' : value.map(valueText).join(', ');
  return JSON.stringify(value);
}

function gatePrompt(question: GateQuestion): string {
  const parts = [
    question.phase === 'before'
      ? "I'm about to run this step. Here is my plan."
      : question.phase === 'after'
        ? 'This step has finished. Please check the result.'
        : `This step failed: ${question.failure?.message ?? 'unknown error'}`,
  ];
  if (question.reasoning.trim() !== '') parts.push(firstSentences(question.reasoning, 3));
  const keys = gateFieldSplit(question).main;
  if (keys.length > 0) {
    parts.push(
      ['Main settings:', ...keys.slice(0, 6).map((key) => `- ${humanize(key)}: ${valueText(question.proposal[key])}`)]
        .join('\n'),
    );
  }
  parts.push('Reply with the buttons below, or tell me in your own words what to change. You can also ask me a question first, for example "what does inferential analysis mean?".');
  return parts.join('\n\n');
}

export default function RunChat({ runId, feed, view, status, mode, forkNote, pending, navigate, onStep, onShowGate, onTab }: Props) {
  const queryClient = useQueryClient();
  const [setup] = useState(() => loadSession(browserStore(), runChatKey(runId)));
  const [showActivity, setShowActivity] = useState(false);
  const [extra, setExtra] = useState<ChatMessage[]>([]);
  const mutation = useMutation<ControlReply, Error, string>({
    mutationFn: (text) => sendMessage(runId, text),
    onSuccess: async (reply) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
        queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
      ]);
      if (reply.newRunId !== null) navigate({ name: 'run', runId: reply.newRunId });
    },
    onError: (error) => {
      const text =
        error instanceof ApiError && error.status === 409
          ? `I can't do that right now because the run is ${STATUS_LABELS[status].toLowerCase()}. A run that waits for your decision can't be paused; answer the decision first.`
          : errorText(error);
      setExtra((list) => [...list, { id: `fault-${list.length}`, role: 'note', tone: 'fault', text }]);
    },
  });

  const answer = useMutation<ControlReply, Error, { gateId: string; revision: number; action: GateAction }>({
    mutationFn: ({ gateId, revision, action }) => answerGate(runId, gateId, { revision, action }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
        queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
      ]);
    },
    onError: (error) => {
      setExtra((list) => [...list, { id: `fault-${list.length}`, role: 'note', tone: 'fault', text: errorText(error) }]);
    },
  });

  const branchTitle = (node: string, candidateId: string | null): string => {
    if (candidateId === null) return nodeLabel(node);
    const label = view?.branches.find((item) => item.id === candidateId)?.label ?? candidateId;
    return `${nodeLabel(node)}: ${humanize(label)}`;
  };

  const messages: ChatMessage[] = [...(setup?.messages ?? [])];
  if (setup !== null) messages.push({ id: 'run-started', role: 'note', text: 'The run started.' });
  messages.push({ id: 'intro', role: 'assistant', text: mode === 'guided' ? INTRO + GUIDED : INTRO });
  if (forkNote !== null) messages.push({ id: 'fork-note', role: 'assistant', text: forkNote });
  for (const item of feed.items) {
    if (!showActivity && item.kind === 'activity') continue;
    const id = `e${item.seq}`;
    switch (item.kind) {
      case 'user':
        messages.push({ id, role: 'user', text: item.text });
        break;
      case 'assistant':
        messages.push({
          id,
          role: 'assistant',
          text: item.text,
          ...(item.newRunId === null
            ? {}
            : { link: { href: formatRoute({ name: 'run', runId: item.newRunId }), label: 'Open the new run' } }),
        });
        break;
      case 'error':
        messages.push({ id, role: 'note', tone: 'fault', text: item.text });
        break;
      case 'activity':
        messages.push({ id, role: 'note', text: item.text });
        break;
      case 'step':
        messages.push({
          id,
          role: 'assistant',
          title: branchTitle(item.node, item.candidateId),
          text: item.text,
          step: { node: item.node, candidateId: item.candidateId },
        });
        break;
      case 'fork':
        messages.push({
          id,
          role: 'note',
          text: 'This run continues as a new run.',
          link: { href: formatRoute({ name: 'run', runId: item.newRunId }), label: `Open run ${item.newRunId}` },
        });
        break;
    }
  }
  if (setup === null && feed.items.length === 0) {
    messages.push({
      id: 'empty',
      role: 'note',
      text: 'No messages yet. Ask the agent about the run, or tell it what to change.',
    });
  }
  const asked = pending.flatMap(({ gate, question }) => (question === null ? [] : [{ gate, question }]));
  for (const { gate, question } of asked) {
    messages.push({
      id: `gate-${gate.id}-${question.revision}`,
      role: 'assistant',
      title: `Your decision: ${branchTitle(question.node, question.candidateId)}`,
      text: gatePrompt(question),
    });
  }
  messages.push(...extra);
  const last = asked.at(-1);

  return (
    <section className="chat-pane" aria-label="Chat with the agent">
      <div className="chat-tools">
        <label>
          <input type="checkbox" checked={showActivity} onChange={(e) => setShowActivity(e.target.checked)} />
          Show step activity
        </label>
      </div>
      <MessageList messages={messages} pending={mutation.isPending} drafts={Object.values(feed.drafts)} onStep={onStep}>
        {last !== undefined && (
          <div className="quick-replies">
            {(['approve', 'approve_all_remaining'] as const).filter((action) => last.question.options.includes(action)).map((action) => (
              <button
                key={action}
                type="button"
                disabled={answer.isPending}
                onClick={() => answer.mutate({ gateId: last.gate.id, revision: last.question.revision, action })}
              >
                {action === 'approve' ? <ThumbsUp className="icon" aria-hidden="true" /> : <FastForward className="icon" aria-hidden="true" />}
                {action === 'approve' ? 'Approve' : 'Approve and run the rest automatically'}
              </button>
            ))}
            <button type="button" onClick={onShowGate}><SlidersHorizontal className="icon" aria-hidden="true" />Review the details</button>
          </div>
        )}
        {status === 'completed' && view !== null && <RunSummary runId={runId} view={view} onTab={onTab} onStep={onStep} />}
      </MessageList>
      <Composer
        onSend={(text) => mutation.mutate(text)}
        disabled={mutation.isPending}
        placeholder="Ask a question, change the plan, or say pause"
      />
    </section>
  );
}
