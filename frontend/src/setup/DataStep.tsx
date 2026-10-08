import { useState } from 'react';
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { listDatasets, uploadDataset } from '../api/client.ts';
import type { DatasetProfile, JsonObject } from '../api/types.ts';
import { setAt, unsetAt } from '../contract/draft.ts';
import { errorText, formatTime, naValuesFromText, naValuesToText } from '../ui/format.ts';

type Props = {
  projectId: string;
  datasetId: string | null;
  options: JsonObject;
  onSelect: (datasetId: string) => void;
  onOptions: (options: JsonObject) => void;
  profile: UseQueryResult<DatasetProfile, Error>;
  onContinue: () => void;
};

function textOption(options: JsonObject, key: string): string {
  const value = options[key];
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

export default function DataStep({ projectId, datasetId, options, onSelect, onOptions, profile, onContinue }: Props) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [naText, setNaText] = useState('');
  const datasets = useQuery({ queryKey: ['datasets', projectId], queryFn: ({ signal }) => listDatasets(projectId, signal) });
  const upload = useMutation({
    mutationFn: (chosen: File) => uploadDataset(projectId, chosen, chosen.name, options),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: ['datasets', projectId] });
      onSelect(row.id);
    },
  });

  function writeText(key: string, text: string) {
    onOptions(text === '' ? unsetAt(options, [key]) : setAt(options, [key], text));
  }

  function writeHeader(text: string) {
    const parsed = Number.parseInt(text, 10);
    onOptions(text === '' || Number.isNaN(parsed) ? unsetAt(options, ['header_row']) : setAt(options, ['header_row'], parsed));
  }

  function writeNa(text: string) {
    setNaText(text);
    const values = naValuesFromText(text);
    onOptions(text.trim() === '' ? unsetAt(options, ['na_values']) : setAt(options, ['na_values'], values));
  }

  return (
    <div>
      <h2>Choose data</h2>
      {datasets.isError && <p role="alert">{errorText(datasets.error)}</p>}
      {datasets.isSuccess && datasets.data.length > 0 && (
        <fieldset>
          <legend>Use a dataset you uploaded</legend>
          {datasets.data.map((row) => (
            <label key={row.id} className="choice">
              <input type="radio" name="dataset" checked={row.id === datasetId} onChange={() => onSelect(row.id)} />
              {`${row.filename} — ${row.n_rows} × ${row.n_cols}, uploaded ${formatTime(row.created_at)}`}
            </label>
          ))}
        </fieldset>
      )}
      <fieldset>
        <legend>Upload a new CSV</legend>
        <div className="field">
          <label htmlFor="csv-file">CSV file</label>
          <input
            id="csv-file" type="file" accept=".csv,text/csv"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </div>
        <details>
          <summary>Parsing options</summary>
          <div className="field">
            <label htmlFor="opt-delimiter">Delimiter</label>
            <input
              id="opt-delimiter" type="text" maxLength={4} placeholder="," value={textOption(options, 'delimiter')}
              onChange={(event) => writeText('delimiter', event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="opt-encoding">Encoding</label>
            <input
              id="opt-encoding" type="text" placeholder="utf-8" value={textOption(options, 'encoding')}
              onChange={(event) => writeText('encoding', event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="opt-decimal">Decimal mark</label>
            <input
              id="opt-decimal" type="text" maxLength={1} placeholder="." value={textOption(options, 'decimal')}
              onChange={(event) => writeText('decimal', event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="opt-header">Header row</label>
            <input
              id="opt-header" type="number" min={0} placeholder="0" value={textOption(options, 'header_row')}
              onChange={(event) => writeHeader(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="opt-na">Missing value markers</label>
            <textarea
              id="opt-na" rows={5} value={naText} aria-describedby="opt-na-help"
              placeholder={naValuesToText(['', 'NA', 'N/A', 'null', 'NULL'])}
              onChange={(event) => writeNa(event.target.value)}
            />
            <p className="help" id="opt-na-help">One per line. (empty) stands for an empty cell.</p>
          </div>
        </details>
        {upload.isError && <p role="alert">{errorText(upload.error)}</p>}
        <div className="actions">
          <button
            type="button" disabled={file === null || upload.isPending}
            onClick={() => { if (file !== null) upload.mutate(file); }}
          >
            Upload CSV
          </button>
        </div>
      </fieldset>
      {datasetId !== null && (
        <div className="panel">
          {profile.isPending && <p>Reading columns…</p>}
          {profile.isError && <p role="alert">{errorText(profile.error)}</p>}
          {profile.isSuccess && (
            <>
              <p>{profile.data.rows} rows, {profile.data.cols} columns</p>
              <p>
                {profile.data.columns.slice(0, 12).join(', ')}
                {profile.data.columns.length > 12 ? ` and ${profile.data.columns.length - 12} more` : ''}
              </p>
            </>
          )}
        </div>
      )}
      <div className="actions">
        <button type="button" className="primary" disabled={!profile.isSuccess} onClick={onContinue}>Continue</button>
      </div>
    </div>
  );
}
