import { useEffect, useRef, type ReactNode } from 'react';
import type { ChatMessage } from './session.ts';

function Link({ message }: { message: ChatMessage }) {
  return message.link === undefined ? null : <> <a href={message.link.href}>{message.link.label}</a></>;
}

type Step = { node: string; candidateId: string | null };
type Props = {
  messages: ChatMessage[];
  pending: boolean;
  drafts?: string[];
  onStep?: (step: Step) => void;
  children?: ReactNode;
};

function Paragraphs({ text }: { text: string }) {
  return <>{text.split(/\n\s*\n/).map((part, index) => <p key={index}>{part}</p>)}</>;
}

export default function MessageList({ messages, pending, drafts = [], onStep, children }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const draftLength = drafts.join('').length;
  useEffect(() => {
    const element = scroller.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
  }, [messages.length, draftLength, pending]);

  return (
    <div className="chat-scroll" ref={scroller}>
      <ol className="messages" aria-live="polite">
        {messages.map((message) => {
          if (message.role === 'assistant') {
            return (
              <li className="msg agent" key={message.id}>
                <span className="avatar" aria-hidden="true" />
                <div className="msg-body">
                  {message.title !== undefined && <p className="msg-title">{message.title}</p>}
                  <Paragraphs text={message.text} />
                  <Link message={message} />
                  {message.step !== undefined && onStep !== undefined && (
                    <button type="button" className="link-button" onClick={() => onStep(message.step as Step)}>
                      Show the details
                    </button>
                  )}
                </div>
              </li>
            );
          }
          if (message.role === 'user') {
            return (
              <li className="msg user" key={message.id}>
                <div className="msg-body"><Paragraphs text={message.text} /><Link message={message} /></div>
              </li>
            );
          }
          return (
            <li className={message.tone === 'fault' ? 'msg note fault' : 'msg note'} key={message.id}>{message.text}<Link message={message} /></li>
          );
        })}
        {drafts.map((draft, index) => (
          <li className="msg agent draft" key={`draft-${index}`}>
            <span className="avatar" aria-hidden="true" />
            <div className="msg-body"><Paragraphs text={draft} /></div>
          </li>
        ))}
        {pending && (
          <li className="msg agent typing" aria-label="The assistant is thinking">
            <span className="avatar" aria-hidden="true" />
            <span className="dots"><i /><i /><i /></span>
          </li>
        )}
      </ol>
      <div className="widget-slot">{children}</div>
    </div>
  );
}
