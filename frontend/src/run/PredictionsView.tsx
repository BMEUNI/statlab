import { useMutation } from '@tanstack/react-query';
import { predictUnlabelled, runFileUrl } from '../api/client.ts';
import { errorText } from '../ui/format.ts';
import { cellText } from './compare.ts';

export default function PredictionsView({ runId, ready }: { runId: string; ready: boolean }) {
  const predict = useMutation({ mutationFn: () => predictUnlabelled(runId) });
  if (!ready) {
    return <p className="secondary-line">Predictions are available once the run has chosen its champion model.</p>;
  }
  const d = predict.data;
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
        {d !== undefined && (
          <a className="button" href={runFileUrl(runId, 'predictions.csv')} download>
            Download CSV
          </a>
        )}
      </div>
      {predict.isError && <p role="alert">{errorText(predict.error)}</p>}
      {d !== undefined && (
        <>
          <p className="secondary-line">
            {`${d.rows} rows predicted.`}
            {d.rows > d.head.length ? ` The first ${d.head.length} are shown; download the CSV for all of them.` : ''}
          </p>
          <div className="table-wrap">
            <table className="validation-table">
              <caption>Predicted rows</caption>
              <thead>
                <tr>
                  {d.columns.map((c) => (
                    <th scope="col" key={c}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.head.map((r) => (
                  <tr key={cellText(r.row ?? null)}>
                    {d.columns.map((c) => (
                      <td key={c}>{cellText(r[c] ?? null)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
