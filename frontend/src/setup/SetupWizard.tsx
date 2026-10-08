import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getDatasetProfile, getProject, getVocabulary, listDatasets } from '../api/client.ts';
import type { JsonObject, SetupValidation } from '../api/types.ts';
import { setAt } from '../contract/draft.ts';
import type { Route } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import CheckStep from './CheckStep.tsx';
import DataStep from './DataStep.tsx';
import EditStep from './EditStep.tsx';
import SourceStep from './SourceStep.tsx';
import StartStep from './StartStep.tsx';

type Step = 1 | 2 | 3 | 4 | 5;
const LABELS = ['Choose data', 'Choose a starting contract', 'Edit the contract', 'Check against the data', 'Start the run'];
const STEPS: Step[] = [1, 2, 3, 4, 5];

export default function SetupWizard({ projectId, navigate }: { projectId: string; navigate: (route: Route) => void }) {
  const [step, setStep] = useState<Step>(1);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [datasetOptions, setDatasetOptions] = useState<JsonObject>({});
  const [draft, setDraft] = useState<JsonObject | null>(null);
  const [reasoning, setReasoning] = useState<string | null>(null);
  const [validation, setValidation] = useState<SetupValidation | null>(null);
  const vocabulary = useQuery({ queryKey: ['vocabulary'], queryFn: ({ signal }) => getVocabulary(signal) });
  useQuery({ queryKey: ['project', projectId], queryFn: ({ signal }) => getProject(projectId, signal) });
  const profile = useQuery({
    queryKey: ['profile', projectId, datasetId, JSON.stringify(datasetOptions)],
    enabled: datasetId !== null,
    queryFn: ({ signal }) =>
      datasetId === null
        ? Promise.reject(new Error('Dataset is required.'))
        : getDatasetProfile(projectId, datasetId, datasetOptions, signal),
  });
  const datasets = useQuery({ queryKey: ['datasets', projectId], queryFn: ({ signal }) => listDatasets(projectId, signal) });

  function selectDataset(id: string) {
    if (id !== datasetId) {
      setDraft(null);
      setReasoning(null);
      setValidation(null);
    }
    setDatasetId(id);
  }

  function chooseDraft(next: JsonObject, why: string | null) {
    setDraft(Object.keys(datasetOptions).length > 0 ? setAt(next, ['dataset'], datasetOptions) : next);
    setReasoning(why);
    setValidation(null);
    setStep(3);
  }

  const loadFailure = vocabulary.isError ? vocabulary : profile.isError ? profile : null;
  const profileData = profile.data;
  const vocabularyData = vocabulary.data;

  function content() {
    if (step === 1 || datasetId === null || profileData === undefined) {
      return (
        <DataStep
          projectId={projectId} datasetId={datasetId} options={datasetOptions} onSelect={selectDataset}
          onOptions={setDatasetOptions} profile={profile} onContinue={() => setStep(2)}
        />
      );
    }
    if (step === 2) {
      return <SourceStep projectId={projectId} datasetId={datasetId} profile={profileData} onDraft={chooseDraft} />;
    }
    if (draft === null || vocabularyData === undefined) return <p>Loading the contract vocabulary…</p>;
    if (step === 3) {
      return (
        <EditStep
          draft={draft} onDraft={setDraft} profile={profileData} vocabulary={vocabularyData} reasoning={reasoning}
          onBack={() => setStep(2)}
          onContinue={() => {
            setValidation(null);
            setStep(4);
          }}
        />
      );
    }
    if (step === 4) {
      return (
        <CheckStep
          projectId={projectId} datasetId={datasetId} draft={draft} validation={validation} onValidation={setValidation}
          onBack={() => setStep(3)} onContinue={() => setStep(5)}
        />
      );
    }
    return (
      <StartStep
        projectId={projectId} datasetId={datasetId} datasetName={datasetName(datasetId)} draft={draft}
        navigate={navigate}
      />
    );
  }

  function datasetName(id: string): string {
    const rows = datasets.data;
    return rows?.find((row) => row.id === id)?.filename ?? id;
  }

  return (
    <>
      <h1>New run</h1>
      {loadFailure !== null && (
        <div role="alert">
          <p>{errorText(loadFailure.error)}</p>
          <button type="button" onClick={() => void loadFailure.refetch()}>Try again</button>
        </div>
      )}
      <div className="wizard">
        <ol className="steps">
          {STEPS.map((number) => {
            const label = LABELS[number - 1];
            if (number === step) return <li key={number} aria-current="step">{label}</li>;
            if (number < step) {
              return <li key={number}><button type="button" onClick={() => setStep(number)}>{label}</button></li>;
            }
            return <li key={number}>{label}</li>;
          })}
        </ol>
        <div>{content()}</div>
      </div>
    </>
  );
}
