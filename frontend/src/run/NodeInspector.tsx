import { X } from 'lucide-react';
import { formatTime } from '../ui/format.ts';
import type { ArtifactEntry, RunExecutions } from '../api/types.ts';
import { ARTIFACT_HEADERS, ArtifactRow } from './ArtifactBrowser.tsx';
import DecisionView from './DecisionView.tsx';
import { executionsFor, formatUsd, latestOutcome, metricLabel, nodeLabel, type RunView } from './model.ts';
import { metricsOf, type MetricValue } from './compare.ts';
import type { Selection } from './RunTimeline.tsx';

type Props = {
  view: RunView;
  data: RunExecutions | undefined;
  selected: Selection | null;
  artifacts: ArtifactEntry[] | undefined;
  onClose: () => void;
};

export default function NodeInspector({ view, data, selected, artifacts, onClose }: Props) {
  if (selected === null) {
    return (
      <section className="panel">
        <p className="secondary-line">Select a step to see what the agent decided, which tools it ran and what it cost.</p>
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
          {attempts.map(({ execution, toolCalls, llmCalls }, index) => (
            <details key={execution.id} open={index === 0}>
              <summary>{`Attempt ${execution.attempt}: ${execution.status}, ${formatTime(execution.startedAt)}`}</summary>
              {execution.endedAt !== null && (
                <p>{`${((Date.parse(execution.endedAt) - Date.parse(execution.startedAt)) / 1000).toFixed(1)} s`}</p>
              )}
              {execution.error && <p role="alert">{execution.error}</p>}
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
              {llmCalls.length > 0 && (
                <>
                  <div className="table-wrap">
                    <table>
                      <caption>Agent calls</caption>
                      <thead>
                        <tr>
                          <th scope="col">Model</th><th scope="col">Prompt tokens</th><th scope="col">Completion tokens</th>
                          <th scope="col">Cached tokens</th><th scope="col">Cost</th><th scope="col">Time (ms)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {llmCalls.map((call) => (
                          <tr key={call.id}>
                            <td>{call.model}</td><td>{call.promptTokens}</td><td>{call.completionTokens}</td>
                            <td>{call.cachedTokens}</td><td>{formatUsd(call.costUsd)}</td><td>{call.durationMs}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p>Agent cost for this attempt: {formatUsd(llmCalls.reduce((sum, call) => sum + call.costUsd, 0))}</p>
                </>
              )}
            </details>
          ))}
        </>
      )}
    </section>
  );
}
