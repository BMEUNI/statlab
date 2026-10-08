import type { DatasetProfile, JsonObject } from '../api/types.ts';
import { columnRows, setIdColumn, setIncluded, setRole, setTarget } from '../contract/draft.ts';

type Props = {
  draft: JsonObject;
  onDraft: (draft: JsonObject) => void;
  profile: DatasetProfile;
  roleOptions: string[];
};

export default function MappingTable({ draft, onDraft, profile, roleOptions }: Props) {
  return (
    <div className="table-wrap">
      <table>
        <caption>Columns</caption>
        <thead>
          <tr>
            <th>Target</th><th>Column</th><th>Type</th><th>Detected role</th><th>Role</th>
            <th>Use</th><th>ID</th><th>Missing</th><th>Distinct</th>
          </tr>
        </thead>
        <tbody>
          {columnRows(profile, draft).map((row) => (
            <tr key={row.name} className={row.isTarget ? 'is-target' : undefined}>
              <td>
                <input
                  type="radio" name="target" checked={row.isTarget} aria-label={`Target ${row.name}`}
                  onChange={() => onDraft(setTarget(draft, row.name))}
                />
              </td>
              <td>{row.name}</td>
              <td>{row.dtype}</td>
              <td>{row.inferredRole ?? '—'}</td>
              <td>
                <select
                  aria-label={`Role ${row.name}`} value={row.role ?? ''} disabled={row.isTarget}
                  onChange={(event) => onDraft(setRole(draft, row.name, event.target.value === '' ? null : event.target.value))}
                >
                  <option value="">{`Detected (${row.inferredRole ?? 'unknown'})`}</option>
                  {roleOptions.map((role) => <option key={role} value={role}>{role}</option>)}
                </select>
              </td>
              <td>
                <input
                  type="checkbox" aria-label={`Use ${row.name}`} checked={row.included} disabled={row.isTarget}
                  onChange={(event) => onDraft(setIncluded(draft, row.name, event.target.checked))}
                />
              </td>
              <td>
                <input
                  type="checkbox" aria-label={`ID ${row.name}`} checked={row.isId} disabled={row.isTarget}
                  onChange={(event) => onDraft(setIdColumn(draft, row.name, event.target.checked))}
                />
              </td>
              <td>{row.nulls ?? '—'}</td>
              <td>{row.unique ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
