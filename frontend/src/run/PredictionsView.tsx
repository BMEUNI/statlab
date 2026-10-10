import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { predictUnlabelled, predictUpload, runFileUrl } from '../api/client.ts';
import type { PredictionPreview } from '../api/types.ts';
import { errorText } from '../ui/format.ts';
import { cellText } from './compare.ts';

function Preview({ runId, preview }: { runId: string; preview: PredictionPreview }) {
  return (
    <>
      <p className="secondary-line">
        {`${preview.rows} rows predicted.`}
        {preview.rows > preview.head.length ? ` The first ${preview.head.length} are shown; download the CSV for all of them.` : ''}
      </p>
      {preview.missing.length > 0 && (
        <p className="help">{`Not in your file, treated as empty: ${preview.missing.join(', ')}.`}</p>
      )}
      {preview.ignored.length > 0 && (
        <p className="help">{`Not used by this run, ignored: ${preview.ignored.join(', ')}.`}</p>
      )}
      <p>
        <a className="button" href={runFileUrl(runId, preview.file)} download>
          Download CSV
        </a>
      </p>
      <div className="table-wrap">
        <table className="validation-table">
          <caption>Predicted rows</caption>
          <thead>
            <tr>
              {preview.columns.map((c) => (
                <th scope="col" key={c}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.head.map((r) => (
              <tr key={cellText(r.row ?? null)}>
                {preview.columns.map((c) => (
                  <td key={c}>{cellText(r[c] ?? null)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function PredictionsView({ runId, ready }: { runId: string; ready: boolean }) {
  const predict = useMutation({ mutationFn: () => predictUnlabelled(runId) });
  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({ mutationFn: (chosen: File) => predictUpload(runId, chosen, chosen.name) });
  if (!ready) {
    return <p className="secondary-line">Predictions are available once the run has chosen its champion model.</p>;
  }
  return (
    <section className="validation" aria-label="Predictions">
      <p className="help">
        The champion model predicts every row of your dataset whose target is empty. Rows removed before the split
        (duplicates, rows with too many missing values) are not included.
      </p>
      <div className="validation-bar">
        <button type="button" className="primary" disabled={predict.isPending} onClick={() => predict.mutate()}>
          {predict.isPending ? 'Predicting…' : 'Predict rows without a target'}
        </button>
      </div>
      {predict.isError && <p role="alert">{errorText(predict.error)}</p>}
      {predict.data !== undefined && <Preview runId={runId} preview={predict.data} />}
      <h3>Predict a new file</h3>
      <p className="help">
        Upload a CSV with the dataset&apos;s columns; the target can be left out. Its rows get the same preparation as
        the dataset (placed after the dataset&apos;s rows, so lag features see its history), then the champion predicts
        them.
      </p>
      <div className="validation-bar">
        <input
          type="file"
          accept=".csv,text/csv"
          aria-label="CSV file to predict"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          className="primary"
          disabled={file === null || upload.isPending}
          onClick={() => {
            if (file !== null) upload.mutate(file);
          }}
        >
          {upload.isPending ? 'Predicting…' : 'Predict this file'}
        </button>
      </div>
      {upload.isError && <p role="alert">{errorText(upload.error)}</p>}
      {upload.data !== undefined && <Preview runId={runId} preview={upload.data} />}
    </section>
  );
}
