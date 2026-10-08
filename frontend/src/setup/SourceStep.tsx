import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { listContracts, proposeContract } from '../api/client.ts';
import type { DatasetProfile, JsonObject } from '../api/types.ts';
import { getAt, parseContractText } from '../contract/draft.ts';
import { errorText, formatTime } from '../ui/format.ts';

type Props = {
  projectId: string;
  datasetId: string;
  profile: DatasetProfile;
  onDraft: (draft: JsonObject, reasoning: string | null) => void;
};
type Source = 'write' | 'agent' | 'saved' | 'file';
const SOURCES: { id: Source; label: string; text: string }[] = [
  { id: 'write', label: 'Write it myself', text: 'Pick the target column, then set the rest yourself.' },
  {
    id: 'agent', label: 'Let the agent draft it',
    text: 'The agent reads the column profile and proposes a full contract. Uses one LLM call.',
  },
  { id: 'saved', label: 'Reuse a saved contract', text: 'Start from a contract saved in this project.' },
  { id: 'file', label: 'Upload a JSON file', text: 'Use a contract file you already have.' },
];

export default function SourceStep({ projectId, datasetId, profile, onDraft }: Props) {
  const [source, setSource] = useState<Source>('write');
  const [column, setColumn] = useState('');
  const [savedId, setSavedId] = useState<string | null>(null);
  const [fileContract, setFileContract] = useState<JsonObject | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const contracts = useQuery({
    queryKey: ['contracts', projectId],
    enabled: source === 'saved',
    queryFn: ({ signal }) => listContracts(projectId, signal),
  });
  const propose = useMutation({
    mutationFn: () => proposeContract(projectId, datasetId, { target: { column } }),
    onSuccess: (proposal) => onDraft(proposal.contract, proposal.reasoning),
  });

  async function readFile(file: File | undefined) {
    setFileContract(null);
    setFileError(null);
    if (file === undefined) return;
    const parsed = parseContractText(await file.text());
    if (parsed.ok) setFileContract(parsed.contract);
    else setFileError(parsed.error);
  }

  const targetSelect = (
    <div className="field">
      <label htmlFor="source-target">Target column</label>
      <select id="source-target" required value={column} onChange={(event) => setColumn(event.target.value)}>
        <option value="">Select a column</option>
        {profile.columns.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
    </div>
  );

  return (
    <div>
      <h2>Choose a starting contract</h2>
      <fieldset>
        <legend>How do you want to start?</legend>
        {SOURCES.map((option) => (
          <label key={option.id} className="choice">
            <input type="radio" name="source" checked={source === option.id} onChange={() => setSource(option.id)} />
            {option.label}
            <small>{option.text}</small>
          </label>
        ))}
      </fieldset>
      {(source === 'write' || source === 'agent') && targetSelect}
      {source === 'write' && (
        <div className="actions">
          <button type="button" className="primary" disabled={column === ''} onClick={() => onDraft({ target: { column } }, null)}>
            Continue
          </button>
        </div>
      )}
      {source === 'agent' && (
        <>
          {propose.isPending && <p>The agent is drafting the contract…</p>}
          {propose.isError && <p role="alert">{errorText(propose.error)}</p>}
          <div className="actions">
            <button type="button" className="primary" disabled={column === '' || propose.isPending} onClick={() => propose.mutate()}>
              Draft with the agent
            </button>
          </div>
        </>
      )}
      {source === 'saved' && (
        <>
          {contracts.isPending && <p>Loading saved contracts…</p>}
          {contracts.isError && <p role="alert">{errorText(contracts.error)}</p>}
          {contracts.isSuccess && contracts.data.length === 0 && <p>No saved contracts in this project.</p>}
          {contracts.isSuccess && contracts.data.length > 0 && (
            <fieldset>
              <legend>Saved contracts</legend>
              {contracts.data.map((record) => {
                const target = getAt(record.contract, ['target', 'column']);
                return (
                  <label key={record.id} className="choice">
                    <input type="radio" name="saved" checked={savedId === record.id} onChange={() => setSavedId(record.id)} />
                    {`Version ${record.version} — target ${typeof target === 'string' ? target : '—'}, saved ${formatTime(record.created_at)}`}
                  </label>
                );
              })}
            </fieldset>
          )}
          <div className="actions">
            <button
              type="button" className="primary" disabled={savedId === null}
              onClick={() => {
                const record = contracts.data?.find((row) => row.id === savedId);
                if (record !== undefined) onDraft(record.contract, null);
              }}
            >
              Use this contract
            </button>
          </div>
        </>
      )}
      {source === 'file' && (
        <>
          <div className="field">
            <label htmlFor="source-file">Contract file</label>
            <input
              id="source-file" type="file" accept=".json,application/json"
              onChange={(event) => { void readFile(event.target.files?.[0]); }}
            />
          </div>
          {fileError !== null && <p role="alert">{fileError}</p>}
          <div className="actions">
            <button
              type="button" className="primary" disabled={fileContract === null}
              onClick={() => { if (fileContract !== null) onDraft(fileContract, null); }}
            >
              Use this file
            </button>
          </div>
        </>
      )}
    </div>
  );
}
