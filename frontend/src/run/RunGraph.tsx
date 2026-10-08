import type { ExecutionRecord, GateQuestion } from '../api/types.ts';
import {
  cellStatus,
  CELL_LABELS,
  graphLayout,
  latestOutcome,
  mergedMetrics,
  nodeLabel,
  type CellStatus,
  type RunView,
  type SpineNode,
} from './model.ts';

export type Selection = { node: string; candidateId: string | null };

type Props = {
  view: RunView;
  executions: ExecutionRecord[];
  pending: GateQuestion[];
  selected: Selection | null;
  onSelect: (next: Selection) => void;
};

function Mark({ status }: { status: CellStatus }) {
  return <span className={`mark mark-${status}`}>{CELL_LABELS[status]}</span>;
}

export default function RunGraph({ view, executions, pending, selected, onSelect }: Props) {
  const layout = graphLayout(view.spine);
  const isSelected = (node: string, candidateId: string | null) =>
    selected !== null && selected.node === node && selected.candidateId === candidateId;

  const sharedTable = (caption: string, nodes: SpineNode[]) => (
    <div className="table-wrap">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr><th scope="col">Step</th><th scope="col">Status</th><th scope="col">Note</th></tr>
        </thead>
        <tbody>
          {nodes.map((node) => {
            const outcome = latestOutcome(view, node.name, null);
            return (
              <tr key={node.name}>
                <td>
                  <button
                    type="button"
                    className="cell-link"
                    aria-pressed={isSelected(node.name, null)}
                    onClick={() => onSelect({ node: node.name, candidateId: null })}
                  >{nodeLabel(node.name)}</button>
                  {node.name === view.overlap && <small className="help">Runs alongside the candidates.</small>}
                </td>
                <td><Mark status={cellStatus(node.name, null, executions, pending, outcome)} /></td>
                <td>{outcome?.skipReason ?? outcome?.error?.message ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  return (
    <>
      {layout.before.length > 0 && sharedTable('Shared steps', layout.before)}
      {layout.perCandidate.length > 0 && (view.branches.length === 0
        ? <p className="secondary-line">Candidate models appear after the experiment design step.</p>
        : (
          <div className="table-wrap">
            <table>
              <caption>Candidate models</caption>
              <thead>
                <tr>
                  <th scope="col">Step</th>
                  {view.branches.map((branch) => (
                    <th scope="col" key={branch.id}>
                      {branch.label}
                      <small className="help">
                        {branch.id}{branch.estimator !== null && branch.estimator !== branch.label ? `, ${branch.estimator}` : ''}{branch.retuneCount > 0 ? `, retuned ${branch.retuneCount} times` : ''}
                      </small>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {layout.perCandidate.map((node) => (
                  <tr key={node.name}>
                    <th scope="row">{nodeLabel(node.name)}</th>
                    {view.branches.map((branch) => (
                      <td key={branch.id}>
                        <button
                          type="button"
                          className="cell-link"
                          aria-pressed={isSelected(node.name, branch.id)}
                          aria-label={`${nodeLabel(node.name)} for ${branch.label}`}
                          onClick={() => onSelect({ node: node.name, candidateId: branch.id })}
                        >
                          <Mark status={cellStatus(node.name, branch.id, executions, pending, latestOutcome(view, node.name, branch.id))} />
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
                <tr>
                  <th scope="row">Validation metrics</th>
                  {view.branches.map((branch) => {
                    const entries = Object.entries(mergedMetrics(branch));
                    return (
                      <td key={branch.id}>
                        {entries.length === 0
                          ? '—'
                          : entries.map(([key, value]) => <div key={key}>{`${key} ${value.toFixed(4)}`}</div>)}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        ))}
      {layout.after.length > 0 && sharedTable('Final steps', layout.after)}
    </>
  );
}
