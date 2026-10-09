import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { getValidation } from '../api/client.ts';
import { errorText } from '../ui/format.ts';
import { cellText, validationModels, validationSummary } from './compare.ts';
import type { RunView } from './model.ts';

const PAGE = 50;

export default function ValidationView({ runId, view }: { runId: string; view: RunView }) {
  const models = validationModels(view);
  const [chosen, setChosen] = useState<string | null>(null);
  const [worstFirst, setWorstFirst] = useState(false);
  const [offset, setOffset] = useState(0);
  const candidate = chosen ?? (models.find((m) => m.champion) ?? models[0])?.id ?? null;
  const page = useQuery({
    queryKey: ['validation', runId, candidate, worstFirst, offset],
    queryFn: ({ signal }) => getValidation(runId, { candidate: candidate ?? '', worstFirst, offset, limit: PAGE }, signal),
    enabled: candidate !== null,
  });

  if (candidate === null) return <p className="secondary-line">No model has validation predictions yet.</p>;

  const d = page.data;
  return (
    <section className="validation" aria-label="Validation predictions">
      <div className="validation-bar">
        <label>
          Model{' '}
          <select value={candidate} onChange={(e) => { setChosen(e.target.value); setOffset(0); }}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>{`${m.label}${m.champion ? ' (champion)' : ''}`}</option>
            ))}
          </select>
        </label>
        <div className="chips" role="group" aria-label="Rows to show">
          <button type="button" className="chip" aria-pressed={!worstFirst} onClick={() => { setWorstFirst(false); setOffset(0); }}>
            All rows
          </button>
          <button type="button" className="chip" aria-pressed={worstFirst} onClick={() => { setWorstFirst(true); setOffset(0); }}>
            {d?.task === 'regression' ? 'Largest errors first' : 'Mistakes only'}
          </button>
        </div>
      </div>
      {page.isPending && <p>Loading validation rows…</p>}
      {page.isError && <p role="alert">{errorText(page.error)}</p>}
      {d && (
        <>
          <p className="secondary-line">{validationSummary(d)}</p>
          <div className="table-wrap">
            <table className="validation-table">
              <caption>
                {d.shownTotal === 0 ? 'No rows to show' : `Rows ${d.offset + 1}–${d.offset + d.rows.length} of ${d.shownTotal}`}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col">Actual</th>
                  <th scope="col">Predicted</th>
                  <th scope="col">{d.task === 'classification' ? 'Confidence' : 'Error'}</th>
                  {d.columns.map((c) => (
                    <th scope="col" key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={cellText(r.row)} className={r.correct === false ? 'miss' : undefined}>
                    <td>{cellText(r.row)}</td>
                    <td>{cellText(r.actual)}</td>
                    <td>
                      {r.correct === null ? cellText(r.predicted) : (
                        <span className={r.correct ? 'pill signal' : 'pill fault'}>{cellText(r.predicted)}</span>
                      )}
                    </td>
                    <td className="num">
                      {d.task === 'classification'
                        ? (r.confidence === null ? '—' : `${(r.confidence * 100).toFixed(1)}%`)
                        : cellText(r.error)}
                    </td>
                    {d.columns.map((c) => (
                      <td key={c}>{cellText(r.values[c] ?? null)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <button type="button" disabled={offset + PAGE >= d.shownTotal} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        </>
      )}
    </section>
  );
}
