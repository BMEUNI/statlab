import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, sendMessage } from '../api/client.ts';
import type { ControlReply, GateQuestion, GateSummary, RunMode, RunStatus } from '../api/types.ts';
import { STATUS_LABELS, humanize, nodeLabel, splitLead, type RunFeed, type RunView } from '../run/model.ts';
import DecisionBox from '../run/DecisionBox.tsx';
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
  now: string;
  onStep: (step: Step) => void;
  onTab: (tab: 'candidates' | 'report') => void;
};

const INTRO = `I'll post a short update here after each step, and a summary of the results at the end.

You can write to me at any time, also while I wait for your decision:
- ask why I did something ("why did you drop LoanID?")
- change a setting ("use 10 folds", "set the test size to 0.3")
- redo part of the run ("go back to data profiling")
- control the run ("pause", "resume", "stop")`;
const GUIDED =
  '\n\nIn guided mode I stop and ask you before each key decision. Answer in the box below the chat, or in your own words.';

export default function RunChat({
  runId, feed, view, status, mode, forkNote, pending, navigate, now, onStep, onTab,
}: Props) {
  const queryClient = useQueryClient();
  const [setup] = useState(() => loadSession(browserStore(), runChatKey(runId)));
  const [showActivity, setShowActivity] = useState(false);
  const [extra, setExtra] = useState<ChatMessage[]>([]);
  const [sent, setSent] = useState(0);
  const [openGate, setOpenGate] = useState<string | null>(null);
  const mutation = useMutation<ControlReply, Error, string>({
    mutationFn: (text) => sendMessage(runId, text),
    onSuccess: async (reply) => {
      setExtra([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
        queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
      ]);
      if (reply.newRunId !== null) navigate({ name: 'run', runId: reply.newRunId });
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 502 || error.status === 503)) return;
      const text =
        error instanceof ApiError && error.status === 409
          ? `I can't do that right now because the run is ${STATUS_LABELS[status].toLowerCase()}. A run that waits for your decision can't be paused; answer the decision first.`
          : errorText(error);
      setExtra((list) => [...list, { id: `fault-${list.length}`, role: 'note', tone: 'fault', text }]);
    },
  });

  const branchTitle = (node: string, candidateId: string | null): string => {
    if (candidateId === null) return nodeLabel(node);
    const label = view?.branches.find((item) => item.id === candidateId)?.label ?? candidateId;
    return `${nodeLabel(node)}: ${humanize(label)}`;
  };

  const messages: ChatMessage[] = [...(setup?.messages ?? [])];
  const tail: ChatMessage[] = [];
  if (setup !== null) messages.push({ id: 'run-started', role: 'note', text: 'The run started.' });
  messages.push({ id: 'intro', role: 'assistant', text: mode === 'guided' ? INTRO + GUIDED : INTRO });
  if (forkNote !== null) messages.push({ id: 'fork-note', role: 'assistant', text: forkNote });
  for (const item of feed.items) {
    if (!showActivity && item.kind === 'activity') continue;
    const target = status === 'completed' && feed.completedSeq !== null && item.seq > feed.completedSeq ? tail : messages;
    const id = `e${item.seq}`;
    switch (item.kind) {
      case 'user':
        target.push({ id, role: 'user', text: item.text });
        break;
      case 'assistant':
        target.push({
          id,
          role: 'assistant',
          text: item.text,
          ...(item.newRunId === null
            ? {}
            : { link: { href: formatRoute({ name: 'run', runId: item.newRunId }), label: 'Open the new run' } }),
        });
        break;
      case 'error':
        target.push({ id, role: 'note', tone: 'fault', text: item.text });
        break;
      case 'activity':
        target.push({ id, role: 'note', text: item.text });
        break;
      case 'step': {
        const { lead, more } = splitLead(item.text);
        target.push({
          id,
          role: 'assistant',
          title: item.title ?? branchTitle(item.node, item.candidateId),
          text: lead,
          ...(more === null ? {} : { more }),
          step: { node: item.node, candidateId: item.candidateId },
        });
        break;
      }
      case 'fork':
        target.push({
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
  (status === 'completed' ? tail : messages).push(...extra);

  return (
    <section className="chat-pane" aria-label="Chat with the agent">
      <div className="chat-tools">
        <p className="chat-now" aria-live="polite">{now}</p>
        <label>
          <input type="checkbox" checked={showActivity} onChange={(e) => setShowActivity(e.target.checked)} />
          Show step activity
        </label>
      </div>
      <MessageList
        messages={messages}
        pending={mutation.isPending}
        drafts={Object.values(feed.drafts)}
        onStep={onStep}
        followKey={sent}
        tail={status === 'completed' ? tail : undefined}
      >
        {status === 'completed' && view !== null && <RunSummary runId={runId} view={view} onTab={onTab} onStep={onStep} />}
      </MessageList>
      {pending.length > 0 && (
        <div className="decision-dock">
          <DecisionBox
            runId={runId}
            mode={mode}
            pending={pending}
            view={view}
            navigate={navigate}
            openGate={openGate}
            onOpenGate={setOpenGate}
          />
        </div>
      )}
      <Composer
        onSend={(text) => {
          setSent((count) => count + 1);
          mutation.mutate(text);
        }}
        disabled={mutation.isPending}
        placeholder="Ask a question, change the plan, or say pause"
      />
    </section>
  );
}
