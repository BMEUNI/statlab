import { useEffect, useId, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAdminSettings, saveAdminSettings } from '../api/client.ts';
import { errorText } from '../ui/format.ts';
import { limitText, parseLimit } from './budget.ts';

export default function AdminDefaults() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['admin', 'settings'], queryFn: ({ signal }) => getAdminSettings(signal) });
  const monthlyId = useId();
  const projectId = useId();
  const runId = useId();
  const signupId = useId();
  const [monthly, setMonthly] = useState('');
  const [project, setProject] = useState('');
  const [run, setRun] = useState('');
  const [signupOpen, setSignupOpen] = useState(false);

  useEffect(() => {
    if (settings.data === undefined) return;
    setMonthly(limitText(settings.data.default_monthly_limit_usd));
    setProject(limitText(settings.data.default_project_limit_usd));
    setRun(limitText(settings.data.default_run_limit_usd));
    setSignupOpen(settings.data.signup_open);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (values: { monthly: number | null; project: number | null; run: number | null; signupOpen: boolean }) =>
      saveAdminSettings({
        default_monthly_limit_usd: values.monthly,
        default_project_limit_usd: values.project,
        default_run_limit_usd: values.run,
        signup_open: values.signupOpen,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });

  if (settings.isPending) return <p>Loading…</p>;
  if (settings.isError) return <p role="alert">{errorText(settings.error)}</p>;

  const parsedMonthly = parseLimit(monthly);
  const parsedProject = parseLimit(project);
  const parsedRun = parseLimit(run);
  const anyInvalid = parsedMonthly === 'invalid' || parsedProject === 'invalid' || parsedRun === 'invalid';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (parsedMonthly === 'invalid' || parsedProject === 'invalid' || parsedRun === 'invalid') return;
    save.mutate({ monthly: parsedMonthly, project: parsedProject, run: parsedRun, signupOpen });
  }

  return (
    <form className="panel defaults-form" onSubmit={submit}>
      <h2>Defaults</h2>
      <p className="help">Used when a person, project or run has no limit of its own. Leave a field empty for no limit.</p>
      <div className="field">
        <label htmlFor={monthlyId}>Monthly limit per person</label>
        <span className="limit-prefix" aria-hidden="true">$</span>
        <input id={monthlyId} type="text" inputMode="decimal" value={monthly} onChange={(event) => setMonthly(event.target.value)} aria-invalid={parsedMonthly === 'invalid'} />
        <p className="help">Resets on the 1st of each month (UTC).</p>
      </div>
      <div className="field">
        <label htmlFor={projectId}>Lifetime limit per project</label>
        <span className="limit-prefix" aria-hidden="true">$</span>
        <input id={projectId} type="text" inputMode="decimal" value={project} onChange={(event) => setProject(event.target.value)} aria-invalid={parsedProject === 'invalid'} />
        <p className="help">Counts every run in the project.</p>
      </div>
      <div className="field">
        <label htmlFor={runId}>Limit per run</label>
        <span className="limit-prefix" aria-hidden="true">$</span>
        <input id={runId} type="text" inputMode="decimal" value={run} onChange={(event) => setRun(event.target.value)} aria-invalid={parsedRun === 'invalid'} />
        <p className="help">A run pauses when it reaches this.</p>
      </div>
      <div className="field check-field">
        <label htmlFor={signupId}>Anyone can create an account</label>
        <input id={signupId} type="checkbox" checked={signupOpen} onChange={(event) => setSignupOpen(event.target.checked)} />
        <p className="help">When off, only existing accounts can sign in. The first account can always be created.</p>
      </div>
      {save.isError && <p role="alert">{errorText(save.error)}</p>}
      {save.isSuccess && <p role="status">Defaults saved.</p>}
      <div className="actions">
        <button className="primary" type="submit" disabled={save.isPending || anyInvalid}>Save defaults</button>
      </div>
    </form>
  );
}
