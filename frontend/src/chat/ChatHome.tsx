import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { assistantTurn, createRun } from '../api/client.ts';
import type { AssistantReply, SetupState } from '../api/types.ts';
import type { Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import Composer from './Composer.tsx';
import MessageList from './MessageList.tsx';
import {
  CURRENT_KEY, browserStore, clearSession, loadSession, newId, newSession, nextStep, runChatKey, saveSession, toTurns,
  type ChatMessage, type SetupSession,
} from './session.ts';
import SetupPane from './SetupPane.tsx';
import SetupWidget from './SetupWidgets.tsx';

type Props = { navigate: (route: Route) => void };

export default function ChatHome({ navigate }: Props) {
  const store = browserStore();
  const client = useQueryClient();
  const [session, setSession] = useState<SetupSession>(() => loadSession(store, CURRENT_KEY) ?? newSession());
  const [pane, setPane] = useState<'chat' | 'side'>('chat');

  useEffect(() => {
    saveSession(store, CURRENT_KEY, session);
  }, [store, session]);

  function append(...added: ChatMessage[]) {
    setSession((current) => ({ ...current, messages: [...current.messages, ...added] }));
  }

  function fault(text: string) {
    append({ id: newId(), role: 'note', tone: 'fault', text });
  }

  const starter = useMutation({
    mutationFn: (ids: { pid: string; did: string; cid: string; mode: NonNullable<SetupState['mode']> }) =>
      createRun(ids.pid, ids.mode, ids.did, ids.cid),
    onSuccess: (runId) => {
      saveSession(store, runChatKey(runId), session);
      clearSession(store, CURRENT_KEY);
      navigate({ name: 'run', runId });
    },
    onError: (error) => fault(errorText(error)),
  });

  function start(state: SetupState) {
    const { project_id: pid, dataset_id: did, contract_id: cid, mode } = state;
    if (pid === null || did === null || cid === null || state.target === null || mode === null) {
      append({ id: newId(), role: 'note', text: 'Something is still missing before the run can start.' });
      return;
    }
    starter.mutate({ pid, did, cid, mode });
  }

  const turn = useMutation({
    mutationFn: (input: { messages: ChatMessage[]; state: SetupState }) =>
      assistantTurn(toTurns(input.messages), input.state),
    onSuccess: (reply: AssistantReply) => {
      setSession((current) => ({
        messages: [
          ...current.messages,
          { id: newId(), role: 'assistant', text: reply.reply },
          ...reply.issues.map((issue): ChatMessage => ({ id: newId(), role: 'note', text: issue })),
        ],
        state: reply.state,
      }));
      void client.invalidateQueries({ queryKey: ['projects'] });
      const pid = reply.state.project_id;
      if (pid !== null) {
        void client.invalidateQueries({ queryKey: ['datasets', pid] });
        void client.invalidateQueries({ queryKey: ['contracts', pid] });
        void client.invalidateQueries({ queryKey: ['project', pid] });
      }
      if (reply.start) start(reply.state);
    },
    onError: (error) => fault(`${errorText(error)} Send your message again to retry.`),
  });

  function send(text: string, next: SetupState | null) {
    const messages: ChatMessage[] = [...session.messages, { id: newId(), role: 'user', text }];
    const state = next ?? session.state;
    setSession({ messages, state });
    turn.mutate({ messages, state });
  }

  const { state, messages } = session;
  return (
    <div className={`split ${pane === 'side' ? 'show-side' : ''}`}>
      <div className="pane-switch" role="tablist">
        <button type="button" role="tab" aria-selected={pane === 'chat'} onClick={() => setPane('chat')}>Chat</button>
        <button type="button" role="tab" aria-selected={pane === 'side'} onClick={() => setPane('side')}>Your run</button>
      </div>
      <section className="chat-pane" aria-label="Chat with the assistant">
        <MessageList messages={messages} pending={turn.isPending}>
          {!turn.isPending && (
            <SetupWidget
              step={nextStep(state)}
              state={state}
              disabled={starter.isPending}
              onPick={(next, text) => send(text, next)}
              onStart={() => start(state)}
            />
          )}
        </MessageList>
        <Composer onSend={(text) => send(text, null)} disabled={turn.isPending} placeholder="Message the assistant" />
      </section>
      <SetupPane
        state={state}
        onState={(next) => setSession((current) => ({ ...current, state: next }))}
        onStart={() => start(state)}
        starting={starter.isPending}
      />
    </div>
  );
}
