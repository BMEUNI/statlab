import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useRef } from 'react';
import { Trophy, X } from 'lucide-react';
import type { ExecutionRecord, GateQuestion } from '../api/types.ts';
import {
  cellStatus,
  CELL_LABELS,
  estimatorLabel,
  graphLayout,
  groupTone,
  humanize,
  latestOutcome,
  metricLabel,
  nodeLabel,
  stepShortLabel,
  TONES,
  type RunView,
  type SpineNode,
} from './model.ts';
import { candidateRows } from './compare.ts';
import { NodeIcon, StatusIcon } from '../ui/icons.tsx';

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
  const rows = candidateRows(view);
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    detailRef.current?.scrollIntoView({ block: 'nearest' });
  }, [selected?.node, selected?.candidateId]);
  const isSelected = (node: string, candidateId: string | null) =>
    selected !== null && selected.node === node && selected.candidateId === candidateId;
  const toggle = (node: string, candidateId: string | null) =>
    onSelect(isSelected(node, candidateId) ? null : { node, candidateId });
  const statusOf = (node: string, candidateId: string | null) =>
    cellStatus(node, candidateId, executions, pending, latestOutcome(view, node, candidateId));
  const detailBlock = () => (
    <div className="tl-detail" ref={detailRef}>
      <button type="button" className="link-button tl-close" onClick={() => onSelect(null)}>
        <X className="icon" aria-hidden="true" />Close the details
      </button>
      {detail}
    </div>
  );

  const shared = (node: SpineNode) => {
    const status = statusOf(node.name, null);
    const outcome = latestOutcome(view, node.name, null);
    const note = outcome?.skipReason ?? outcome?.error?.message ?? null;
    const isSel = isSelected(node.name, null);
    return (
      <li className={`tl-item tl-${TONES[status]}`} key={node.name}>
        <span className="tl-marker" aria-hidden="true"><NodeIcon node={node.name} /></span>
        <div className="tl-body">
          <div className="tl-head">
            <h3>
              <button type="button" className="tl-toggle" aria-expanded={isSel} onClick={() => toggle(node.name, null)}>
                {nodeLabel(node.name)}
              </button>
            </h3>
            <span className={`mark mark-${status}`}><StatusIcon status={status} />{CELL_LABELS[status]}</span>
          </div>
          {node.name === view.overlap && <small className="help">Runs alongside the candidates.</small>}
          {note !== null && <p className="tl-note">{note}</p>}
          {isSel && detailBlock()}
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
            {view.branches.length > 0 && (
              <p className="tl-note">Each model goes through these steps in order: {layout.perCandidate.map((n) => stepShortLabel(n.name)).join(', then ')}.</p>
            )}
            {view.branches.length === 0
              ? <p className="tl-note">Candidate models appear after the experiment design step.</p>
              : (
                <ul className="lanes">
                  {view.branches.map((branch) => {
                    const row = rows.find((item) => item.id === branch.id);
                    const score = row?.primaryMetric ? row.metrics[row.primaryMetric] : undefined;
                    const isChampion = branch.id === championId;
                    const n = layout.perCandidate.length;
                    const done = layout.perCandidate.filter((node) => {
                      const s = statusOf(node.name, branch.id);
                      return s === 'completed' || s === 'skipped';
                    }).length;
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
                            <p className="lane-progress">{done} of {n} steps done</p>
                          </div>
                          {isChampion && <span className="pill signal"><Trophy className="icon" aria-hidden="true" />Champion</span>}
                          <p className="lane-metric">
                            {score === undefined || row?.primaryMetric == null
                              ? 'Not scored yet'
                              : `${metricLabel(row.primaryMetric)} ${score.value.toFixed(3)}`}
                          </p>
                        </div>
                        <ol className="stepper" style={{ '--steps': n } as CSSProperties} aria-label={`Steps for ${humanize(branch.label)}`}>
                          {layout.perCandidate.map((node) => {
                            const status = statusOf(node.name, branch.id);
                            return (
                              <li key={node.name} className={`step step-${status}`}>
                                <button
                                  type="button"
                                  className="step-button"
                                  title={`${nodeLabel(node.name)}: ${CELL_LABELS[status]}`}
                                  aria-pressed={isSelected(node.name, branch.id)}
                                  onClick={() => toggle(node.name, branch.id)}
                                >
                                  <span className="step-dot"><StatusIcon status={status} /></span>
                                  <span className="step-name">{stepShortLabel(node.name)}</span>
                                  <span className="sr-only">{nodeLabel(node.name)}: {CELL_LABELS[status]}</span>
                                </button>
                              </li>
                            );
                          })}
                        </ol>
                        {showDetail && detailBlock()}
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
