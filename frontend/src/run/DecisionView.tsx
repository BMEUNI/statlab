import { Fragment } from 'react';
import type { JsonObject, JsonValue } from '../api/types.ts';
import { nodeLabel } from './model.ts';

function cell(value: JsonValue) {
  if (typeof value === 'string') return <span className="reasoning">{value}</span>;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value === null) return '—';
  if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
    return value.length === 0 ? 'None' : value.join(', ');
  }
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}

export default function DecisionView({ value }: { value: JsonObject }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <p className="secondary-line">Nothing recorded.</p>;
  return (
    <dl className="facts">
      {entries.map(([key, item]) => (
        <Fragment key={key}>
          <dt>{nodeLabel(key)}</dt>
          <dd>{cell(item)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}
