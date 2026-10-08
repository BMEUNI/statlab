import { Send } from 'lucide-react';
import { useState, type FormEvent, type KeyboardEvent } from 'react';

type Props = { onSend: (text: string) => void; disabled: boolean; placeholder: string };

export default function Composer({ onSend, disabled, placeholder }: Props) {
  const [text, setText] = useState('');
  const trimmed = text.trim();

  function submit() {
    if (disabled || trimmed === '') return;
    onSend(trimmed);
    setText('');
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form className="composer" onSubmit={(event: FormEvent) => { event.preventDefault(); submit(); }}>
      <label className="sr-only" htmlFor="composer-input">Message</label>
      <textarea
        id="composer-input"
        rows={1}
        value={text}
        placeholder={placeholder}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" className="primary" disabled={disabled || trimmed === ''}><Send className="icon" aria-hidden="true" />Send</button>
    </form>
  );
}
