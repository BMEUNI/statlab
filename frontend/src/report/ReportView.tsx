import { useQuery } from '@tanstack/react-query';
import { ApiError, artifactUrl, getRunFile, runFileUrl } from '../api/client.ts';
import type { RunSummary } from '../api/types.ts';
import { formatMetric } from '../run/compare.ts';
import type { RunView } from '../run/model.ts';
import { errorText } from '../ui/format.ts';
import { champion, plotImageUrl } from './champion.ts';
import Markdown from './Markdown.tsx';

const DOWNLOADS: Array<[string, string]> = [
  ['Report (report.md)', 'report.md'],
  ['Pipeline script (pipeline.py)', 'pipeline.py'],
  ['Run manifest (manifest.json)', 'manifest.json'],
  ['Contract used (contract.snapshot.json)', 'contract.snapshot.json'],
  ['Agent instructions used (skill.snapshot.md)', 'skill.snapshot.md'],
];

export default function ReportView({ run, view }: { run: RunSummary; view: RunView }) {
  const report = useQuery({
    queryKey: ['file', run.id, 'report.md'],
    queryFn: ({ signal }) => getRunFile(run.id, 'report.md', signal),
    retry: false,
  });
  const best = champion(view);
  return (
    <>
      {best !== null && (
        <section className="panel champion" aria-labelledby="champion-heading">
          <h2 id="champion-heading">Champion</h2>
          <dl>
            <dt>Candidate</dt><dd>{best.label} <code>{best.candidateId}</code></dd>
            <dt>Estimator</dt><dd>{best.estimator ?? '—'}</dd>
            <dt>{`Validation ${best.primaryMetric ?? ''}`.trim()}</dt>
            <dd>{best.validation === null ? '—' : formatMetric(best.validation)}</dd>
            <dt>Test scorings before this one</dt><dd>{best.priorScorings ?? '—'}</dd>
            <dt>Search waves</dt><dd>{best.waves ?? '—'}</dd>
          </dl>
          <div className="table-wrap">
            <table>
              <caption>Test set scores</caption>
              <thead><tr><th scope="col">Metric</th><th scope="col">Score</th></tr></thead>
              <tbody>
                {Object.entries(best.testMetrics).map(([name, metric]) => (
                  <tr key={name}><td>{name}</td><td>{formatMetric(metric)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="help">
            The test score is computed once, after selection. Validation scores are optimistic because they were used to choose the champion.
          </p>
          {best.caveats.map((text, index) => <p className="help" key={index}>{text}</p>)}
        </section>
      )}
      <h2>Downloads</h2>
      {run.status === 'completed'
        ? (
          <div className="actions">
            {DOWNLOADS.map(([label, name]) => (
              <a key={name} className="button" href={runFileUrl(run.id, name)} download>{label}</a>
            ))}
            {best?.modelRef != null && <a className="button" href={artifactUrl(best.modelRef)} download>Champion model</a>}
          </div>
        )
        : <p className="secondary-line">Downloads appear when the run completes.</p>}
      <h2>Report</h2>
      {report.isPending && <p>Loading the report…</p>}
      {report.isError && (
        report.error instanceof ApiError && report.error.status === 404
          ? <p>The report appears after champion selection.</p>
          : <p role="alert">{errorText(report.error)}</p>
      )}
      {report.isSuccess && (
        <article className="report"><Markdown source={report.data} imageUrl={plotImageUrl(run.id)} /></article>
      )}
    </>
  );
}
