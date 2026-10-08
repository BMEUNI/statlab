import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { artifactUrl, previewArtifact } from '../api/client.ts';
import type { ArtifactEntry } from '../api/types.ts';
import { errorText, formatTime } from '../ui/format.ts';
import { artifactKinds, artifactSplits, filterArtifacts } from './compare.ts';

export const ARTIFACT_HEADERS = ['File', 'Kind', 'Split', 'Shape', 'Made by', 'Created', 'Actions'];

function ArtifactPreviewTable({ id }: { id: string }) {
  const preview = useQuery({ queryKey: ['preview', id], queryFn: ({ signal }) => previewArtifact(id, signal) });
  if (preview.isPending) return <p>Loading preview…</p>;
  if (preview.isError) return <p role="alert">{errorText(preview.error)}</p>;
  const { rows, cols, columns, dtypes, head } = preview.data;
  return (
    <div className="table-wrap">
      <table>
        <caption>{`First ${head.length} of ${rows} rows, ${cols} columns`}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th scope="col" key={column}>{column} <small className="help">{dtypes[column]}</small></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {head.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => {
                const cell = row[column];
                return <td key={column}>{typeof cell === 'object' && cell !== null ? JSON.stringify(cell) : String(cell ?? '')}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArtifactRow({ entry }: { entry: ArtifactEntry }) {
  const [open, setOpen] = useState(false);
  const { id, kind, label, split, rows, cols, locked, pruned } = entry;
  const name = label ?? id;
  const isPlot = kind === 'plot';
  const isTable = kind === 'dataframe' || kind === 'predictions';
  return (
    <>
      <tr>
        <td>{name} <small className="help"><code>{id}</code></small></td>
        <td>{kind}</td>
        <td>{split ?? '—'}</td>
        <td>{rows !== null && cols !== null ? `${rows} × ${cols}` : '—'}</td>
        <td>{entry.producedBy}</td>
        <td>{formatTime(entry.ts)}</td>
        <td>
          {locked && <span className="pill gate">Locked until the test score is recorded</span>}
          {!locked && pruned && <span className="pill">Removed to save space</span>}
          {!locked && !pruned && (
            <>
              <a className="button" href={artifactUrl(id)} download>Download</a>
              {isPlot && (
                <button type="button" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'}</button>
              )}
              {isTable && (
                <button type="button" onClick={() => setOpen(!open)}>{open ? 'Hide preview' : 'Preview'}</button>
              )}
            </>
          )}
        </td>
      </tr>
      {open && !locked && !pruned && (
        <tr>
          <td colSpan={7}>
            {isPlot && <img className="plot" alt={name} src={artifactUrl(id)} />}
            {isTable && <ArtifactPreviewTable id={id} />}
          </td>
        </tr>
      )}
    </>
  );
}

export default function ArtifactBrowser({ entries }: { entries: ArtifactEntry[] }) {
  const [kind, setKind] = useState('');
  const [split, setSplit] = useState('');
  if (entries.length === 0) return <p className="secondary-line">No files yet. Steps add files as they run.</p>;
  const filtered = filterArtifacts(entries, kind, split);
  return (
    <>
      <div className="filters">
        <div>
          <label htmlFor="artifact-kind">Kind</label>
          <select id="artifact-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">All</option>
            {artifactKinds(entries).map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="artifact-split">Split</label>
          <select id="artifact-split" value={split} onChange={(event) => setSplit(event.target.value)}>
            <option value="">All</option>
            {artifactSplits(entries).map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
      </div>
      <p>{`${filtered.length} of ${entries.length} files`}</p>
      <div className="table-wrap">
        <table>
          <caption>Run files</caption>
          <thead><tr>{ARTIFACT_HEADERS.map((header) => <th scope="col" key={header}>{header}</th>)}</tr></thead>
          <tbody>{filtered.map((entry) => <ArtifactRow key={entry.id} entry={entry} />)}</tbody>
        </table>
      </div>
    </>
  );
}
