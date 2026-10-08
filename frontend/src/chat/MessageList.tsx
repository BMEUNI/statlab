import { ArrowDown, Bot, ScanSearch } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import Markdown from '../report/Markdown.tsx';
import { jsonBlock } from '../run/model.ts';
import type { ChatMessage } from './session.ts';

function Link({ message }: { message: ChatMessage }) {
  return message.link === undefined ? null : <> <a href={message.link.href}>{message.link.label}</a></>;
}

type Step = { node: string; candidateId: string | null };
type Props = {
  messages: ChatMessage[];
  tail?: ChatMessage[] | undefined;
  pending: boolean;
  drafts?: string[];
  onStep?: (step: Step) => void;
  followKey?: number;
  children?: ReactNode;
};

function Paragraphs({ text }: { text: string }) {
  return <>{text.split(/\n\s*\n/).map((part, index) => <p key={index}>{part}</p>)}</>;
}

function RichText({ text }: { text: string }) {
  const json = jsonBlock(text);
  return json === null ? <Markdown source={text} imageUrl={() => null} /> : <pre className="json-view">{json}</pre>;
}

export default function MessageList({ messages, tail, pending, drafts = [], onStep, followKey = 0, children }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [unseen, setUnseen] = useState(false);
  const draftLength = drafts.join('').length;
  const jump = () => {
    const element = scroller.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
    atBottom.current = true;
    setUnseen(false);
  };
  useLayoutEffect(() => {
    if (atBottom.current) {
      jump();
    } else {
      setUnseen(true);
    }
  }, [messages.length, draftLength, pending]); // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (followKey > 0) jump();
  }, [followKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const element = scroller.current;
    if (element === null) return undefined;
    const observer = new ResizeObserver(() => {
      const node = scroller.current;
      if (node !== null && atBottom.current) node.scrollTop = node.scrollHeight;
    });
    observer.observe(element);
    for (const child of Array.from(element.children)) observer.observe(child);
    return () => observer.disconnect();
  }, []);

  const renderMessage = (message: ChatMessage) => {
    if (message.role === 'assistant') {
      return (
        <li className="msg agent" key={message.id}>
          <span className="avatar" aria-hidden="true"><Bot className="icon" aria-hidden="true" /></span>
          <div className="msg-body">
            {message.title !== undefined && <p className="msg-title">{message.title}</p>}
            <RichText text={message.text} />
            {message.more !== undefined && (
              <details className="msg-more">
                <summary>More</summary>
                <RichText text={message.more} />
              </details>
            )}
            <Link message={message} />
            {message.step !== undefined && onStep !== undefined && (
              <button type="button" className="link-button" onClick={() => onStep(message.step as Step)}>
                <ScanSearch className="icon" aria-hidden="true" /> Show the details
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
  };

  const trailing = (
    <>
      {drafts.map((draft, index) => (
        <li className="msg agent draft" key={`draft-${index}`}>
          <span className="avatar" aria-hidden="true"><Bot className="icon" aria-hidden="true" /></span>
          <div className="msg-body"><Paragraphs text={draft} /></div>
        </li>
      ))}
      {pending && (
        <li className="msg agent typing" aria-label="The assistant is thinking">
          <span className="avatar" aria-hidden="true"><Bot className="icon" aria-hidden="true" /></span>
          <span className="dots"><i /><i /><i /></span>
        </li>
      )}
    </>
  );

  return (
    <div
      className="chat-scroll"
      ref={scroller}
      onScroll={() => {
        const element = scroller.current;
        if (element === null) return;
        atBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        if (atBottom.current) setUnseen(false);
      }}
    >
      <ol className="messages" aria-live="polite">
        {messages.map(renderMessage)}
        {tail === undefined && trailing}
      </ol>
      <div className="widget-slot">{children}</div>
      <ol className="messages tail" aria-live="polite">
        {tail?.map(renderMessage)}
        {tail !== undefined && trailing}
      </ol>
      {unseen && (
        <button type="button" className="jump-latest" onClick={jump}>
          <ArrowDown className="icon" aria-hidden="true" />New messages
        </button>
      )}
    </div>
  );
}
