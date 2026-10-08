import { useState } from 'react';
import type { DatasetProfile, JsonObject, Vocabulary } from '../api/types.ts';
import { getAt, parseContractText } from '../contract/draft.ts';
import { fieldsOf } from '../contract/schema.ts';
import MappingTable from './MappingTable.tsx';
import SchemaForm from './SchemaForm.tsx';

type Props = {
  draft: JsonObject;
  onDraft: (draft: JsonObject) => void;
  profile: DatasetProfile;
  vocabulary: Vocabulary;
  reasoning: string | null;
  onBack: () => void;
  onContinue: () => void;
};
type Tab = 'columns' | 'rules' | 'json';
const TABS: { id: Tab; label: string }[] = [
  { id: 'columns', label: 'Columns' },
  { id: 'rules', label: 'Rules' },
  { id: 'json', label: 'JSON' },
];

export default function EditStep({ draft, onDraft, profile, vocabulary, reasoning, onBack, onContinue }: Props) {
  const [tab, setTab] = useState<Tab>('columns');
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);
  const roleOptions = fieldsOf(vocabulary.contractSchema, 'FeatureRules').find((field) => field.key === 'roles')?.options ?? [];

  function open(next: Tab) {
    if (next === 'json') {
      setJsonText(JSON.stringify(draft, null, 2));
      setJsonError(null);
      setApplied(false);
    }
    setTab(next);
  }

  function apply() {
    const parsed = parseContractText(jsonText);
    if (parsed.ok) {
      onDraft(parsed.contract);
      setJsonError(null);
      setApplied(true);
    } else {
      setJsonError(parsed.error);
      setApplied(false);
    }
  }

  return (
    <div>
      <h2>Edit the contract</h2>
      {reasoning !== null && (
        <details open>
          <summary>Why the agent proposed this</summary>
          <p className="reasoning">{reasoning}</p>
        </details>
      )}
      <div className="tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id} type="button" role="tab" id={`tab-${item.id}`} aria-selected={tab === item.id}
            aria-controls="edit-panel" onClick={() => open(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="panel" role="tabpanel" id="edit-panel" aria-labelledby={`tab-${tab}`}>
        {tab === 'columns' && <MappingTable draft={draft} onDraft={onDraft} profile={profile} roleOptions={roleOptions} />}
        {tab === 'rules' && (
          <SchemaForm
            schema={vocabulary.contractSchema} defName={null} path={[]} draft={draft} onDraft={onDraft}
            columns={profile.columns} vocabulary={vocabulary}
          />
        )}
        {tab === 'json' && (
          <>
            <label htmlFor="contract-json">Contract JSON</label>
            <textarea
              id="contract-json" className="json" rows={24} spellCheck={false} value={jsonText}
              onChange={(event) => setJsonText(event.target.value)}
            />
            {jsonError !== null && <p role="alert">{jsonError}</p>}
            {applied && <p className="ok">Applied.</p>}
            <div className="actions"><button type="button" onClick={apply}>Apply JSON</button></div>
          </>
        )}
      </div>
      <div className="actions">
        <button type="button" onClick={onBack}>Back</button>
        <button
          type="button" className="primary" disabled={typeof getAt(draft, ['target', 'column']) !== 'string'}
          onClick={onContinue}
        >
          Check against the data
        </button>
      </div>
    </div>
  );
}
