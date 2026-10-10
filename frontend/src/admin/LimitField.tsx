import { useEffect, useId, useState, type FormEvent } from 'react';
import { limitText, parseLimit } from './budget.ts';

export default function LimitField({ label, value, placeholder, pending, onSave }: {
  label: string;
  value: number | null;
  placeholder: string;
  pending: boolean;
  onSave: (value: number | null) => void;
}) {
  const inputId = useId();
  const [draft, setDraft] = useState(limitText(value));
  useEffect(() => {
    setDraft(limitText(value));
  }, [value]);
  const parsed = parseLimit(draft);
  const dirty = draft.trim() !== limitText(value);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (parsed !== 'invalid') onSave(parsed);
  }

  return (
    <form className="limit-field" onSubmit={submit}>
      <label className="sr-only" htmlFor={inputId}>{label}</label>
      <span className="limit-prefix" aria-hidden="true">$</span>
      <input
        id={inputId}
        type="text"
        inputMode="decimal"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder}
        aria-invalid={parsed === 'invalid'}
      />
      {dirty && (
        <button className="limit-save" type="submit" disabled={pending || parsed === 'invalid'}>Save</button>
      )}
    </form>
  );
}
