import { useState } from 'react';
import { defaultSort, formatMetric, metricColumns, sortRows, type CandidateRow, type SortKey } from './compare.ts';

type Header = { metric: string | null; text: string; primary: boolean };

export default function CandidateTable({ rows }: { rows: CandidateRow[] }) {
  const [sort, setSort] = useState<SortKey>(() => defaultSort(rows));
  if (rows.length === 0) return <p className="secondary-line">Candidate models appear after the experiment design step.</p>;
  const columns = metricColumns(rows);
  const primary = defaultSort(rows).metric;

  function choose(metric: string | null) {
    if (sort.metric === metric) {
      setSort({ metric, direction: sort.direction === 'asc' ? 'desc' : 'asc' });
      return;
    }
    const lowerBetter = metric !== null && rows.find((row) => row.greaterIsBetter === false) !== undefined;
    setSort({ metric, direction: metric === null || lowerBetter ? 'asc' : 'desc' });
  }

  const headers: Header[] = [
    { metric: null, text: 'Rank', primary: false },
    ...columns.map((name) => ({ metric: name, text: name, primary: name === primary })),
  ];
  function sortable(header: Header) {
    const active = sort.metric === header.metric;
    const ariaSort = active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none';
    return (
      <th scope="col" key={header.text} aria-sort={ariaSort}>
        <button type="button" className="cell-link" onClick={() => choose(header.metric)}>{header.text}</button>
        {header.primary && <small className="help">primary</small>}
      </th>
    );
  }

  return (
    <>
      <p className="help">Validation scores with bootstrap intervals. Select a column to sort.</p>
      <div className="table-wrap">
        <table>
          <caption>Candidate comparison</caption>
          <thead>
            <tr>
              {sortable(headers[0] as Header)}
              <th scope="col">Candidate</th>
              <th scope="col">Estimator</th>
              <th scope="col">Differs from baseline</th>
              <th scope="col">Status</th>
              {headers.slice(1).map(sortable)}
              <th scope="col">Eligible</th>
            </tr>
          </thead>
          <tbody>
            {sortRows(rows, sort).map((row) => (
              <tr key={row.id} className={row.champion ? 'is-target' : undefined}>
                <td>{row.rank ?? '—'}</td>
                <td>
                  {row.rationale === null
                    ? row.label
                    : <details><summary>{row.label}</summary><p className="reasoning">{row.rationale}</p></details>}
                  {row.champion && <> <span className="pill signal">Champion</span></>}
                  <small className="help">{row.id}</small>
                </td>
                <td>{row.estimator ?? '—'}</td>
                <td>{row.isBaseline ? 'Baseline' : row.varies.join(', ') || '—'}</td>
                <td>{row.status}</td>
                {columns.map((name) => {
                  const metric = row.metrics[name];
                  return <td key={name}>{metric === undefined ? '—' : formatMetric(metric)}</td>;
                })}
                <td>
                  {row.eligible === null ? '—' : row.eligible ? 'Yes' : `No: ${row.ineligibleReason ?? 'not eligible'}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
