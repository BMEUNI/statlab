import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getDatasetProfile, listDatasets, listProjects, uploadDataset } from '../api/client.ts';
import type { RunMode, SetupState } from '../api/types.ts';
import { errorText } from '../ui/format.ts';
import { EMPTY_STATE, type SetupStep } from './session.ts';

type Props = {
  step: SetupStep;
  state: SetupState;
  disabled: boolean;
  onPick: (next: SetupState | null, text: string) => void;
  onStart: () => void;
};

function ProjectChips({ state, disabled, onPick }: Omit<Props, 'step' | 'onStart'>) {
  const projects = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => listProjects(signal) });
  const shown = [...(projects.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);
  return (
    <>
      {projects.isError && <p role="alert">{errorText(projects.error)}</p>}
      <div className="chips">
        {shown.map((project) => (
          <button
            key={project.id}
            type="button"
            className="chip"
            disabled={disabled}
            onClick={() => onPick({ ...EMPTY_STATE, project_id: project.id, mode: state.mode }, `Let's work on the project "${project.name}".`)}
          >{project.name}</button>
        ))}
      </div>
      <p className="widget-hint">Or type a name to create a new project.</p>
    </>
  );
}

function DatasetChips({ projectId, state, disabled, onPick }: Omit<Props, 'step' | 'onStart'> & { projectId: string }) {
  const client = useQueryClient();
  const datasets = useQuery({ queryKey: ['datasets', projectId], queryFn: ({ signal }) => listDatasets(projectId, signal) });
  const upload = useMutation({
    mutationFn: (file: File) => uploadDataset(projectId, file, file.name, {}),
    onSuccess: (row) => {
      void client.invalidateQueries({ queryKey: ['datasets', projectId] });
      onPick({ project_id: projectId, dataset_id: row.id, target: null, contract_id: null, mode: state.mode }, `I uploaded ${row.filename}.`);
    },
  });
  return (
    <>
      {datasets.isError && <p role="alert">{errorText(datasets.error)}</p>}
      <div className="chips">
        {(datasets.data ?? []).map((dataset) => (
          <button
            key={dataset.id}
            type="button"
            className="chip"
            disabled={disabled}
            onClick={() => onPick({ project_id: projectId, dataset_id: dataset.id, target: null, contract_id: null, mode: state.mode }, `Use the dataset ${dataset.filename}.`)}
          >{dataset.filename}<small>{`${dataset.n_rows} × ${dataset.n_cols}`}</small></button>
        ))}
        <label className="chip upload">
          Upload a CSV
          <input
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            disabled={disabled || upload.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file !== undefined) upload.mutate(file);
            }}
          />
        </label>
      </div>
      {upload.isPending && <p className="widget-hint">Uploading…</p>}
      {upload.isError && <p role="alert">{errorText(upload.error)}</p>}
    </>
  );
}

function TargetChips({ projectId, datasetId, disabled, onPick }: { projectId: string; datasetId: string; disabled: boolean; onPick: Props['onPick'] }) {
  const profile = useQuery({
    queryKey: ['profile', projectId, datasetId, '{}'],
    queryFn: ({ signal }) => getDatasetProfile(projectId, datasetId, {}, signal),
  });
  return (
    <>
      {profile.isError && <p role="alert">{errorText(profile.error)}</p>}
      <div className="chips">
        {(profile.data?.columns ?? []).map((name) => (
          <button
            key={name}
            type="button"
            className="chip"
            disabled={disabled}
            onClick={() => onPick(null, `Predict ${name}.`)}
          >{name}<small>{profile.data?.roles[name] ?? profile.data?.dtypes[name]}</small></button>
        ))}
      </div>
      <p className="widget-hint">Or describe what you want to predict.</p>
    </>
  );
}

export default function SetupWidget({ step, state, disabled, onPick, onStart }: Props) {
  if (step === 'project') return <ProjectChips state={state} disabled={disabled} onPick={onPick} />;
  if (step === 'dataset' && state.project_id !== null) {
    return <DatasetChips projectId={state.project_id} state={state} disabled={disabled} onPick={onPick} />;
  }
  if (step === 'target' && state.project_id !== null && state.dataset_id !== null) {
    return <TargetChips projectId={state.project_id} datasetId={state.dataset_id} disabled={disabled} onPick={onPick} />;
  }
  if (step === 'contract') {
    return <p className="widget-hint">Review the modelling setup on the right, or tell me what to change.</p>;
  }
  if (step === 'mode') {
    const pick = (mode: RunMode) => onPick({ ...state, mode }, mode === 'guided' ? 'Use guided mode.' : 'Use autonomous mode.');
    return (
      <div className="choice-cards">
        <button type="button" disabled={disabled} onClick={() => pick('guided')}>
          Guided<small>I approve each decision the agent makes.</small>
        </button>
        <button type="button" disabled={disabled} onClick={() => pick('autonomous')}>
          Autonomous<small>The agent runs to the end. I can still pause or step in.</small>
        </button>
      </div>
    );
  }
  if (step === 'start') {
    return <button type="button" className="primary" disabled={disabled} onClick={onStart}>Start the run</button>;
  }
  return null;
}
