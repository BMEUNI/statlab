import { Trophy } from 'lucide-react';
import { artifactUrl } from '../api/client.ts';
import { champion } from '../report/champion.ts';
import { estimatorLabel, humanize, metricLabel, nodeLabel, type RunView } from '../run/model.ts';
import { championPlots, confusionMatrix, stepLines, topFeatures, whyChampion } from './summary.ts';

type Step = { node: string; candidateId: string | null };
type Props = {
  runId: string;
  view: RunView;
  onTab: (tab: 'candidates' | 'report') => void;
  onStep: (step: Step) => void;
};

const fmt = (x: number) => x.toFixed(3);

export default function RunSummary({ view, onTab, onStep }: Props) {
  const c = champion(view);
  if (c === null) return null;
  const primary = c.primaryMetric;
  const test = primary === null ? undefined : c.testMetrics[primary];
  const why = whyChampion(view);
  const plots = championPlots(view, c.candidateId);
  const confusion = confusionMatrix(view, c.candidateId);
  const features = topFeatures(view, c.candidateId, 5);
  const max = features === null ? 0 : Math.max(...features.items.map((item) => item.value));
  return (
    <section className="run-summary" aria-label="Run summary">
      <h3><Trophy className="icon" aria-hidden="true" /> The run is finished</h3>
      <p>
        The best model is <strong>{humanize(c.label)}</strong> ({estimatorLabel(c.estimator)}), chosen from{' '}
        {view.branches.length} candidate models.
      </p>
      {primary !== null && test !== undefined && (
        <p>
          On the held-out test set it scored <strong>{metricLabel(primary)} {fmt(test.value)}</strong>
          {test.ciLow !== null && test.ciHigh !== null && ` (95% interval ${fmt(test.ciLow)} to ${fmt(test.ciHigh)})`}
          . This is the only unbiased estimate of how it will do on new data.
        </p>
      )}
      {why !== null && <><h4>Why this model</h4><p>{why}</p></>}
      <h4>What I did</h4>
      <ol className="summary-steps">
        {stepLines(view).map(({ node, text }) => (
          <li key={node}>
            <button type="button" className="link-button" onClick={() => onStep({ node, candidateId: null })}>
              {nodeLabel(node)}
            </button>: {text}
          </li>
        ))}
        <li>
          <button type="button" className="link-button" onClick={() => onTab('candidates')}>Candidate models</button>
          : trained and compared {view.branches.length} models on the same validation rows.
        </li>
      </ol>
      {plots.length > 0 && (
        <>
          <h4>How the best model performs on validation data</h4>
          <div className="summary-plots">
            {plots.map((plot) => (
              <figure key={plot.ref}>
                <a href={artifactUrl(plot.ref)} target="_blank" rel="noreferrer">
                  <img src={artifactUrl(plot.ref)} alt={plot.caption} loading="lazy" />
                </a>
                <figcaption>{plot.caption}</figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {confusion !== null && (
        <table className="confusion">
          <caption>Confusion matrix (validation): rows are the true class, columns the predicted class</caption>
          <thead>
            <tr>
              <th scope="col">True \ predicted</th>
              {confusion.labels.map((label) => <th scope="col" key={label}>{label}</th>)}
            </tr>
          </thead>
          <tbody>
            {confusion.matrix.map((row, index) => (
              <tr key={confusion.labels[index]}>
                <th scope="row">{confusion.labels[index]}</th>
                {row.map((count, column) => <td key={confusion.labels[column]}>{count.toLocaleString()}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {features !== null && (
        <>
          <h4>What drives the predictions</h4>
          <ul className="bars">
            {features.items.map(({ feature, value }) => (
              <li key={feature}>
                <span className="bar-label">{feature}</span>
                <span
                  className="bar"
                  style={{ width: `${max > 0 ? Math.max(0, value) / max * 100 : 0}%` }}
                  aria-hidden="true"
                />
                <span className="sr-only">{value.toFixed(4)}</span>
              </li>
            ))}
          </ul>
          <p className="help">
            How much {metricLabel(features.metric)} drops when the feature is shuffled (permutation importance on
            validation data).
          </p>
        </>
      )}
      <div className="actions">
        <button type="button" className="primary" onClick={() => onTab('report')}>Open the full report</button>
        <button type="button" onClick={() => onTab('candidates')}>Compare all candidates</button>
      </div>
    </section>
  );
}
