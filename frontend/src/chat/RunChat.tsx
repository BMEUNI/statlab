import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendMessage } from '../api/client.ts';
import type { ControlReply } from '../api/types.ts';
import type { RunFeed } from '../run/model.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import Composer from './Composer.tsx';
import MessageList from './MessageList.tsx';
import { browserStore, loadSession, runChatKey, type ChatMessage } from './session.ts';

type Props = { runId: string; feed: RunFeed; waiting: number; navigate: (route: Route) => void };

export default function RunChat({ runId, feed, waiting, navigate }: Props) {
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
      setExtra((list) => [...list, { id: `fault-${list.length}`, role: 'note', tone: 'fault', text: errorText(error) }]);
    },
  });

  const messages: ChatMessage[] = [...(setup?.messages ?? [])];
  if (setup !== null) messages.push({ id: 'run-started', role: 'note', text: 'The run started.' });
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
  messages.push(...extra);

  return (
    <section className="chat-pane" aria-label="Chat with the agent">
      <div className="chat-tools">
        <label>
          <input type="checkbox" checked={showActivity} onChange={(e) => setShowActivity(e.target.checked)} />
          Show step activity
        </label>
      </div>
      <MessageList messages={messages} pending={mutation.isPending} drafts={Object.values(feed.drafts)}>
        {waiting > 0 && <p className="widget-hint">The agent is waiting for your decision in the progress pane.</p>}
      </MessageList>
      <Composer
        onSend={(text) => mutation.mutate(text)}
        disabled={mutation.isPending}
        placeholder="Ask a question, change the plan, or say pause"
      />
    </section>
  );
}
