import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendMessage } from '../api/client.ts';
import type { ControlReply } from '../api/types.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import type { RunFeed } from './model.ts';

type Props = { runId: string; feed: RunFeed; navigate: (route: Route) => void };

export default function ChatPanel({ runId, feed, navigate }: Props) {
  const queryClient = useQueryClient();
  const listRef = useRef<HTMLOListElement>(null);
  const [text, setText] = useState('');
  const [showActivity, setShowActivity] = useState(true);
  const trimmed = text.trim();
  const mutation = useMutation<ControlReply, Error, string>({
    mutationFn: (message) => sendMessage(runId, message),
    onSuccess: async (reply) => {
      setText('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run', runId] }),
        queryClient.invalidateQueries({ queryKey: ['executions', runId] }),
      ]);
      if (reply.newRunId !== null) navigate({ name: 'run', runId: reply.newRunId });
    },
  });
  const submit = () => {
    if (trimmed !== '' && !mutation.isPending) mutation.mutate(trimmed);
  };
  const drafts = Object.entries(feed.drafts);
  const visible = feed.items.filter((item) => showActivity || item.kind !== 'activity');
  const draftLength = Object.values(feed.drafts).join('').length;

  useEffect(() => {
    const list = listRef.current;
    if (list !== null) list.scrollTop = list.scrollHeight;
  }, [feed.items.length, draftLength, showActivity]);

  return (
    <aside className="chat panel" aria-labelledby="chat-heading">
      <h2 id="chat-heading">Conversation</h2>
      <label>
        <input type="checkbox" checked={showActivity} onChange={(e) => setShowActivity(e.target.checked)} />
        Show step activity
      </label>
      <ol className="feed" aria-live="polite" ref={listRef}>
        {visible.map((item) => {
          switch (item.kind) {
            case 'user':
              return (
                <li key={item.seq} className="msg user">
                  <span className="who">You</span><p className="reasoning">{item.text}</p>
                </li>
              );
            case 'assistant':
              return (
                <li key={item.seq} className="msg agent">
                  <span className="who">Agent</span><p className="reasoning">{item.text}</p>
                  {item.newRunId !== null && <a href={formatRoute({ name: 'run', runId: item.newRunId })}>Open the new run</a>}
                </li>
              );
            case 'error':
              return <li key={item.seq} className="msg error" role="alert">{item.text}</li>;
            case 'activity':
              return (
                <li key={item.seq} className="activity">
                  <time>{new Date(item.ts).toLocaleTimeString()}</time> {item.text}
                </li>
              );
            case 'fork':
              return (
                <li key={item.seq} className="activity">
                  This run continues as <a href={formatRoute({ name: 'run', runId: item.newRunId })}>run {item.newRunId}</a>
                </li>
              );
          }
        })}
        {drafts.map(([replyTo, draft]) => (
          <li key={`draft-${replyTo}`} className="msg agent draft">
            <span className="who">Agent is writing…</span><p className="reasoning">{draft}</p>
          </li>
        ))}
        {visible.length === 0 && drafts.length === 0 && (
          <li className="activity">No messages yet. Ask the agent about the run, or tell it what to change.</li>
        )}
      </ol>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="field">
          <label htmlFor="chat-input">Message the agent</label>
          <textarea
            id="chat-input"
            rows={3}
            placeholder="Ask a question, change the plan, or say pause"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                submit();
              }
            }}
          />
        </div>
        <button type="submit" className="primary" disabled={trimmed === '' || mutation.isPending}>Send</button>
        {mutation.isError && <p role="alert">{errorText(mutation.error)}</p>}
      </form>
    </aside>
  );
}
