import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createRun, uploadContract } from '../api/client.ts';
import type { JsonObject, RunMode } from '../api/types.ts';
import { getAt } from '../contract/draft.ts';
import type { Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';

type Props = {
  projectId: string;
  datasetId: string;
  datasetName: string;
  draft: JsonObject;
  navigate: (route: Route) => void;
};

function text(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback;
}

export default function StartStep({ projectId, datasetId, datasetName, draft, navigate }: Props) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<RunMode>('guided');
  const start = useMutation({
    mutationFn: async () => {
      const contract = await uploadContract(projectId, draft);
      return createRun(projectId, mode, datasetId, contract.id);
    },
    onSuccess: async (runId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['contracts', projectId] }),
      ]);
      navigate({ name: 'run', runId });
    },
  });

  return (
    <div>
      <h2>Start the run</h2>
      <dl className="facts">
        <dt>Dataset</dt><dd>{datasetName}</dd>
        <dt>Target</dt><dd>{text(getAt(draft, ['target', 'column']), '—')}</dd>
        <dt>Task</dt><dd>{text(getAt(draft, ['objective', 'task']), 'The agent decides')}</dd>
        <dt>Primary metric</dt><dd>{text(getAt(draft, ['objective', 'primary_metric']), 'The agent decides')}</dd>
      </dl>
      <fieldset>
        <legend>How should the run proceed?</legend>
        <label className="choice">
          <input type="radio" name="mode" checked={mode === 'guided'} onChange={() => setMode('guided')} />
          Guided
          <small>The agent stops before each decision so you can approve, edit or redirect it.</small>
        </label>
        <label className="choice">
          <input type="radio" name="mode" checked={mode === 'autonomous'} onChange={() => setMode('autonomous')} />
          Autonomous
          <small>The agent runs to the end. You can still pause, step in or rewind.</small>
        </label>
      </fieldset>
      {start.isPending && <p>Saving the contract and starting the run…</p>}
      {start.isError && <p role="alert">{errorText(start.error)}</p>}
      <div className="actions">
        <button type="button" className="primary" disabled={start.isPending} onClick={() => start.mutate()}>Start run</button>
      </div>
    </div>
  );
}
