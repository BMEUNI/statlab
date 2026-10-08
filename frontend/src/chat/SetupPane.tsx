import { useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getDatasetProfile, getProject, getVocabulary, listContracts, listDatasets, uploadContract, validateContract,
} from '../api/client.ts';
import type { ContractRecord, JsonObject, JsonValue, SetupState, SetupValidation } from '../api/types.ts';
import { getAt } from '../contract/draft.ts';
import EditStep from '../setup/EditStep.tsx';
import { errorText } from '../ui/format.ts';
import { nextStep } from './session.ts';

type Props = { state: SetupState; onState: (next: SetupState) => void; onStart: () => void; starting: boolean };

function text(value: JsonValue | undefined): string | null {
  return typeof value === 'string' ? value : null;
}

function ContractCard({ projectId, datasetId, record, state, onState }: {
  projectId: string; datasetId: string; record: ContractRecord; state: SetupState; onState: (next: SetupState) => void;
}) {
  const client = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<JsonObject>(record.contract);
  const [validation, setValidation] = useState<SetupValidation | null>(null);
  const contract = record.contract;
  const profile = useQuery({
    queryKey: ['profile', projectId, datasetId, '{}'],
    queryFn: ({ signal }) => getDatasetProfile(projectId, datasetId, {}, signal),
  });
  const vocabulary = useQuery({ queryKey: ['vocabulary'], queryFn: ({ signal }) => getVocabulary(signal) });
  const save = useMutation({
    mutationFn: async () => {
      const checked = await validateContract(projectId, datasetId, draft);
      if (!checked.valid) return { checked, row: null };
      return { checked, row: await uploadContract(projectId, draft) };
    },
    onSuccess: ({ checked, row }) => {
      setValidation(checked);
      if (row === null) return;
      void client.invalidateQueries({ queryKey: ['contracts', projectId] });
      onState({ ...state, contract_id: row.id, target: text(getAt(row.contract, ['target', 'column'])) ?? state.target });
      dialog.current?.close();
    },
  });

  const candidates = getAt(contract, ['modelling', 'candidates']);
  const goals = getAt(contract, ['objective', 'inference_goals']);
  const questions = Array.isArray(goals)
    ? goals.flatMap((goal) => { const q = text(getAt(goal, ['question'])); return q === null ? [] : [q]; })
    : [];
  const targetType = text(getAt(contract, ['target', 'type']));

  function open() {
    setDraft(record.contract);
    setValidation(null);
    save.reset();
    dialog.current?.showModal();
  }

  return (
    <>
      <dl className="facts">
        <dt>Target</dt>
        <dd>{text(getAt(contract, ['target', 'column'])) ?? '—'}{targetType !== null && ` (${targetType})`}</dd>
        <dt>Task</dt>
        <dd>{text(getAt(contract, ['objective', 'task'])) ?? '—'}</dd>
        <dt>Primary metric</dt>
        <dd>{text(getAt(contract, ['objective', 'primary_metric'])) ?? '—'}</dd>
        <dt>Candidate models</dt>
        <dd>{Array.isArray(candidates) && candidates.length > 0 ? candidates.map(String).join(', ') : 'The agent chooses'}</dd>
        <dt>Split</dt>
        <dd>{text(getAt(contract, ['split', 'strategy'])) ?? 'The agent chooses'}</dd>
        <dt>Inference questions</dt>
        <dd>{questions.length > 0 ? <ul>{questions.map((q, index) => <li key={index}>{q}</li>)}</ul> : 'None'}</dd>
      </dl>
      <button type="button" onClick={open}>Edit details</button>
      <dialog className="drawer" ref={dialog}>
        <button type="button" className="close" onClick={() => dialog.current?.close()}>Close</button>
        {(profile.data === undefined || vocabulary.data === undefined) ? <p>Loading the editor…</p> : (
          <EditStep
            draft={draft}
            onDraft={setDraft}
            profile={profile.data}
            vocabulary={vocabulary.data}
            reasoning={null}
            onBack={() => dialog.current?.close()}
            onContinue={() => save.mutate()}
          />
        )}
        {save.isPending && <p>Saving…</p>}
        {save.isError && <p role="alert">{errorText(save.error)}</p>}
        {validation !== null && !validation.valid && (
          <>
            <h3>Fix these before saving</h3>
            <ul>
              {validation.mismatches.map((item, index) => (
                <li key={index}>
                  {typeof item.field === 'string' && typeof item.message === 'string'
                    ? <><strong>{item.field}</strong> {item.message}</>
                    : <code>{JSON.stringify(item)}</code>}
                </li>
              ))}
            </ul>
          </>
        )}
      </dialog>
    </>
  );
}

export default function SetupPane({ state, onState, onStart, starting }: Props) {
  const pid = state.project_id;
  const project = useQuery({
    queryKey: ['project', pid],
    enabled: pid !== null,
    queryFn: ({ signal }) => getProject(pid ?? '', signal),
  });
  const datasets = useQuery({
    queryKey: ['datasets', pid],
    enabled: pid !== null,
    queryFn: ({ signal }) => listDatasets(pid ?? '', signal),
  });
  const contracts = useQuery({
    queryKey: ['contracts', pid],
    enabled: pid !== null,
    queryFn: ({ signal }) => listContracts(pid ?? '', signal),
  });
  const step = nextStep(state);
  const dataset = datasets.data?.find((row) => row.id === state.dataset_id);
  const record = contracts.data?.find((row) => row.id === state.contract_id);
  const none = 'Not chosen yet';
  const items: { key: string; title: string; done: boolean; value: ReactNode }[] = [
    { key: 'project', title: 'Project', done: pid !== null, value: pid === null ? none : (project.data?.project.name ?? pid) },
    {
      key: 'dataset', title: 'Dataset', done: state.dataset_id !== null,
      value: state.dataset_id === null ? none : dataset === undefined ? state.dataset_id : `${dataset.filename}, ${dataset.n_rows} × ${dataset.n_cols}`,
    },
    { key: 'target', title: 'Target', done: state.target !== null, value: state.target ?? none },
    {
      key: 'contract', title: 'Modelling setup', done: state.contract_id !== null,
      value: pid === null || state.dataset_id === null || state.contract_id === null ? none : record === undefined ? 'Loading…' : (
        <ContractCard projectId={pid} datasetId={state.dataset_id} record={record} state={state} onState={onState} />
      ),
    },
    { key: 'mode', title: 'Mode', done: state.mode !== null, value: state.mode === null ? none : state.mode === 'guided' ? 'Guided' : 'Autonomous' },
  ];

  return (
    <aside className="side-pane">
      <h2>Your run</h2>
      <p className="secondary-line">Everything the run needs. Fill it in by chatting, or use the choices in the chat.</p>
      <ol className="timeline">
        {items.map((item) => {
          const status = item.done ? 'done' : step === item.key ? 'current' : 'upcoming';
          return (
            <li className={`tl-item tl-${status}`} key={item.key}>
              <span className="tl-marker" aria-hidden="true" />
              <div className="tl-body"><h3>{item.title}</h3>{item.value}</div>
            </li>
          );
        })}
      </ol>
      {step === 'start' && (
        <div className="ready">
          <p>Ready to start.</p>
          <button type="button" className="primary" disabled={starting} onClick={onStart}>Start the run</button>
        </div>
      )}
    </aside>
  );
}
