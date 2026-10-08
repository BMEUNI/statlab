import type { ReactNode } from 'react';
import type { ExecutionRecord, GateQuestion } from '../api/types.ts';
import {
  cellStatus,
  CELL_LABELS,
  estimatorLabel,
  graphLayout,
  groupTone,
  humanize,
  latestOutcome,
  mergedMetrics,
  metricLabel,
  nodeLabel,
  TONES,
  type RunView,
  type SpineNode,
} from './model.ts';

export type Selection = { node: string; candidateId: string | null };

type Props = {
  view: RunView;
  executions: ExecutionRecord[];
  pending: GateQuestion[];
  selected: Selection | null;
  championId: string | null;
  onSelect: (next: Selection | null) => void;
  detail: ReactNode;
};

export default function RunTimeline({ view, executions, pending, selected, championId, onSelect, detail }: Props) {
  const layout = graphLayout(view.spine);
  const isSelected = (node: string, candidateId: string | null) =>
    selected !== null && selected.node === node && selected.candidateId === candidateId;
  const toggle = (node: string, candidateId: string | null) =>
    onSelect(isSelected(node, candidateId) ? null : { node, candidateId });
  const statusOf = (node: string, candidateId: string | null) =>
    cellStatus(node, candidateId, executions, pending, latestOutcome(view, node, candidateId));

  const shared = (node: SpineNode) => {
    const status = statusOf(node.name, null);
    const outcome = latestOutcome(view, node.name, null);
    const note = outcome?.skipReason ?? outcome?.error?.message ?? null;
    const isSel = isSelected(node.name, null);
    return (
      <li className={`tl-item tl-${TONES[status]}`} key={node.name}>
        <span className="tl-marker" aria-hidden="true" />
        <div className="tl-body">
          <div className="tl-head">
            <h3>
              <button type="button" className="tl-toggle" aria-expanded={isSel} onClick={() => toggle(node.name, null)}>
                {nodeLabel(node.name)}
              </button>
            </h3>
            <span className={`mark mark-${status}`}>{CELL_LABELS[status]}</span>
          </div>
          {node.name === view.overlap && <small className="help">Runs alongside the candidates.</small>}
          {note !== null && <p className="tl-note">{note}</p>}
          {isSel && <div className="tl-detail">{detail}</div>}
        </div>
      </li>
    );
  };

  const allStatuses = layout.perCandidate.flatMap((node) => view.branches.map((branch) => statusOf(node.name, branch.id)));

  return (
    <ol className="timeline run-timeline">
      {layout.before.map(shared)}
      {layout.perCandidate.length > 0 && (
        <li className={`tl-item tl-${groupTone(allStatuses)}`}>
          <span className="tl-marker" aria-hidden="true" />
          <div className="tl-body">
            <div className="tl-head"><h3>Candidate models</h3></div>
            {view.branches.length === 0
              ? <p className="tl-note">Candidate models appear after the experiment design step.</p>
              : (
                <ul className="lanes">
                  {view.branches.map((branch) => {
                    const first = Object.entries(mergedMetrics(branch))[0];
                    const isChampion = branch.id === championId;
                    const showDetail = selected !== null && selected.candidateId === branch.id;
                    return (
                      <li className={`lane${isChampion ? ' is-champion' : ''}`} key={branch.id}>
                        <div className="lane-head">
                          <div className="lane-title">
                            <h4>{humanize(branch.label)}</h4>
                            <p>
                              {estimatorLabel(branch.estimator)}
                              {branch.retuneCount > 0 ? `, retuned ${branch.retuneCount} times` : ''}
                            </p>
                          </div>
                          {isChampion && <span className="pill signal">Champion</span>}
                          <p className="lane-metric">
                            {first === undefined ? 'Not scored yet' : `${metricLabel(first[0])} ${first[1].toFixed(4)}`}
                          </p>
                        </div>
                        <ol className="lane-steps" aria-label={`Steps for ${humanize(branch.label)}`}>
                          {layout.perCandidate.map((node) => {
                            const status = statusOf(node.name, branch.id);
                            return (
                              <li key={node.name}>
                                <button
                                  type="button"
                                  className={`step-chip chip-${status}`}
                                  aria-pressed={isSelected(node.name, branch.id)}
                                  onClick={() => toggle(node.name, branch.id)}
                                >
                                  <span className="chip-icon" aria-hidden="true" />
                                  {nodeLabel(node.name)}
                                  <span className="sr-only">: {CELL_LABELS[status]}</span>
                                </button>
                              </li>
                            );
                          })}
                        </ol>
                        {showDetail && <div className="tl-detail">{detail}</div>}
                      </li>
                    );
                  })}
                </ul>
              )}
          </div>
        </li>
      )}
      {layout.after.map(shared)}
    </ol>
  );
}
