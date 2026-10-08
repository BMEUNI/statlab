import { useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { validateContract } from '../api/client.ts';
import type { JsonObject, SetupValidation } from '../api/types.ts';
import { errorText } from '../ui/format.ts';

type Props = {
  projectId: string;
  datasetId: string;
  draft: JsonObject;
  validation: SetupValidation | null;
  onValidation: (validation: SetupValidation) => void;
  onBack: () => void;
  onContinue: () => void;
};

export default function CheckStep({ projectId, datasetId, draft, validation, onValidation, onBack, onContinue }: Props) {
  const check = useMutation({
    mutationFn: () => validateContract(projectId, datasetId, draft),
    onSuccess: onValidation,
  });
  const { mutate } = check;
  useEffect(() => {
    mutate();
  }, [mutate]);

  return (
    <div>
      <h2>Check against the data</h2>
      {check.isPending && <p>Checking the contract against the data…</p>}
      {check.isError && <p role="alert">{errorText(check.error)}</p>}
      {validation !== null && validation.valid && <p className="ok">The contract matches the data.</p>}
      {validation !== null && !validation.valid && (
        <>
          <h3>Fix these before starting</h3>
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
      {validation !== null && validation.warnings.length > 0 && (
        <>
          <h3>Warnings</h3>
          <ul>{validation.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        </>
      )}
      <div className="actions">
        <button type="button" onClick={onBack}>Back to the contract</button>
        <button type="button" disabled={check.isPending} onClick={() => mutate()}>Check again</button>
        <button type="button" className="primary" disabled={validation?.valid !== true} onClick={onContinue}>Continue</button>
      </div>
    </div>
  );
}
