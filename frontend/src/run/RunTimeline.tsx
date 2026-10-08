import type { ReactNode } from 'react';
import type { ExecutionRecord, GateQuestion } from '../api/types.ts';
import {
  cellStatus,
  CELL_LABELS,
  graphLayout,
  groupTone,
  latestOutcome,
  mergedMetrics,
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
  onSelect: (next: Selection | null) => void;
  detail: ReactNode;
};

export default function RunTimeline({ view, executions, pending, selected, onSelect, detail }: Props) {
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
                <>
                  <p className="tl-note">
                    Steps for each model, in order: {layout.perCandidate.map((node) => nodeLabel(node.name)).join(', ')}.
                  </p>
                  <ul className="lanes">
                    {view.branches.map((branch) => {
                      const entries = Object.entries(mergedMetrics(branch));
                      return (
                        <li className="lane" key={branch.id}>
                          <div className="lane-name">
                            <strong>{branch.label}</strong>
                            <small>
                              {branch.estimator !== null && branch.estimator !== branch.label ? branch.estimator : branch.id}
                              {branch.retuneCount > 0 ? `, retuned ${branch.retuneCount} times` : ''}
                            </small>
                          </div>
                          <div className="lane-steps">
                            {layout.perCandidate.map((node) => {
                              const status = statusOf(node.name, branch.id);
                              const text = `${nodeLabel(node.name)} for ${branch.label}: ${CELL_LABELS[status]}`;
                              return (
                                <button
                                  type="button"
                                  key={node.name}
                                  className={`lane-dot dot-${status}`}
                                  aria-pressed={isSelected(node.name, branch.id)}
                                  aria-label={text}
                                  title={text}
                                  onClick={() => toggle(node.name, branch.id)}
                                />
                              );
                            })}
                          </div>
                          <div className="lane-metrics">
                            {entries.length === 0
                              ? '—'
                              : entries.map(([key, value]) => <div key={key}>{`${key} ${value.toFixed(4)}`}</div>)}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                  {selected !== null && selected.candidateId !== null && <div className="tl-detail">{detail}</div>}
                </>
              )}
          </div>
        </li>
      )}
      {layout.after.map(shared)}
    </ol>
  );
}
