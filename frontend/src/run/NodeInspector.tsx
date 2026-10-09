import { X } from 'lucide-react';
import { formatTime } from '../ui/format.ts';
import type { ArtifactEntry, RunExecutions } from '../api/types.ts';
import { ARTIFACT_HEADERS, ArtifactRow } from './ArtifactBrowser.tsx';
import DecisionView from './DecisionView.tsx';
import { executionsFor, failureText, latestOutcome, metricLabel, nodeLabel, QUALITY_STATUS_LABELS, qualityRows, type QualityStatus, type RunView } from './model.ts';
import { metricsOf, type MetricValue } from './compare.ts';
import type { Selection } from './RunTimeline.tsx';

type Props = {
  view: RunView;
  data: RunExecutions | undefined;
  selected: Selection | null;
  artifacts: ArtifactEntry[] | undefined;
  onClose: () => void;
};

const QUALITY_PILLS: Record<QualityStatus, string> = {
  pass: 'pill signal',
  warn: 'pill gate',
  fail: 'pill fault',
  skipped: 'pill',
};

export default function NodeInspector({ view, data, selected, artifacts, onClose }: Props) {
  if (selected === null) {
    return (
      <section className="panel">
        <p className="secondary-line">Select a step to see what the agent decided and which tools it ran.</p>
      </section>
    );
  }
  const { node, candidateId } = selected;
  const branchLabel = candidateId === null
    ? null
    : view.branches.find((item) => item.id === candidateId)?.label ?? candidateId;
  const outcome = latestOutcome(view, node, candidateId);
  const metrics: Record<string, MetricValue> = outcome === null ? {} : metricsOf(outcome);
  const decision = outcome?.summary.decision;
  const attempts = data === undefined ? null : executionsFor(node, candidateId, data);
  const quality = outcome === null || node !== 'data_quality' ? [] : qualityRows(outcome.summary);

  return (
    <section className="panel" aria-labelledby="inspector-heading">
      <div className="inspector-head">
        <h2 id="inspector-heading">{nodeLabel(node)}{branchLabel === null ? '' : ` for ${branchLabel}`}</h2>
        <button type="button" className="icon-button" aria-label="Close the details" title="Close the details" onClick={onClose}>
          <X className="icon" aria-hidden="true" />
        </button>
      </div>
      <h3>Outcome</h3>
      {outcome === null ? <p>No result recorded yet.</p> : (
        <>
          <dl className="facts">
            <dt>Status</dt><dd>{outcome.status}</dd>
            <dt>Skip reason</dt><dd>{outcome.skipReason ?? '—'}</dd>
            <dt>Error</dt><dd>{outcome.error === null ? '—' : `${outcome.error.errorClass}: ${outcome.error.message}`}</dd>
            <dt>Warnings</dt>
            <dd>{outcome.warnings.length === 0 ? '—' : outcome.warnings.map((warning, index) => <div key={index}>{warning}</div>)}</dd>
          </dl>
          {Object.keys(metrics).length > 0 && (
            <div className="table-wrap">
              <table>
                <caption>Metrics</caption>
                <thead>
                  <tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">95% interval</th></tr>
                </thead>
                <tbody>
                  {Object.entries(metrics).map(([key, metric]) => (
                    <tr key={key}>
                      <td>{metricLabel(key)}</td>
                      <td>{metric.value.toFixed(4)}</td>
                      <td>
                        {metric.ciLow !== null && metric.ciHigh !== null
                          ? `${metric.ciLow.toFixed(3)} to ${metric.ciHigh.toFixed(3)}`
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {quality.length > 0 && (
            <div className="table-wrap">
              <table>
                <caption>Data quality checks</caption>
                <thead>
                  <tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Rows</th><th scope="col">Columns</th><th scope="col">Details</th></tr>
                </thead>
                <tbody>
                  {quality.map((row) => (
                    <tr key={row.name}>
                      <td>{row.label}</td>
                      <td><span className={QUALITY_PILLS[row.status]}>{QUALITY_STATUS_LABELS[row.status]}</span></td>
                      <td>{row.rows}</td>
                      <td>{row.columns.length === 0 ? '—' : row.columns.join(', ')}</td>
                      <td>{row.note === '' ? '—' : row.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {outcome.artifacts.length > 0 && (
            <>
              <h3>Files</h3>
              {artifacts === undefined ? <p>Loading files…</p> : (
                <div className="table-wrap">
                  <table>
                    <caption>Files made by this step</caption>
                    <thead><tr>{ARTIFACT_HEADERS.map((header) => <th scope="col" key={header}>{header}</th>)}</tr></thead>
                    <tbody>
                      {outcome.artifacts.map((ref) => {
                        const entry = artifacts.find((item) => item.id === ref);
                        return entry === undefined
                          ? <tr key={ref}><td colSpan={7}><code>{ref}</code> is not listed for this run.</td></tr>
                          : <ArtifactRow key={ref} entry={entry} />;
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {typeof decision === 'object' && decision !== null && !Array.isArray(decision) && (
            <>
              <h3>Decision</h3>
              <DecisionView value={decision} />
            </>
          )}
        </>
      )}
      {attempts === null && <p>Loading attempts…</p>}
      {attempts !== null && attempts.length === 0 && <p>This step has not run yet.</p>}
      {attempts !== null && attempts.length > 0 && (
        <>
          <h3>Attempts</h3>
          {attempts.map(({ execution, toolCalls }, index) => (
            <details key={execution.id} open={index === 0}>
              <summary>{`Attempt ${execution.attempt}: ${execution.status}, ${formatTime(execution.startedAt)}`}</summary>
              {execution.endedAt !== null && (
                <p>{`${((Date.parse(execution.endedAt) - Date.parse(execution.startedAt)) / 1000).toFixed(1)} s`}</p>
              )}
              {execution.error && <p role="alert">{failureText(execution.error)}</p>}
              {toolCalls.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <caption>Tool calls</caption>
                    <thead><tr><th scope="col">Tool</th><th scope="col">Time (ms)</th><th scope="col">Details</th></tr></thead>
                    <tbody>
                      {toolCalls.map((call) => (
                        <tr key={call.id}>
                          <td>{call.tool}</td>
                          <td>{call.durationMs}</td>
                          <td>
                            <details>
                              <summary>Arguments and result</summary>
                              <pre className="json-view">{JSON.stringify(call.args, null, 2)}</pre>
                              <pre className="json-view">{JSON.stringify(call.result, null, 2)}</pre>
                            </details>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </details>
          ))}
        </>
      )}
    </section>
  );
}
