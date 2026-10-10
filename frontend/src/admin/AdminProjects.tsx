import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { changeProjectOwner, getAdminSettings, listAdminProjects, updateAdminProject } from '../api/client.ts';
import type { ProjectPatch } from '../api/types.ts';
import { formatRoute } from '../routes.ts';
import { errorText } from '../ui/format.ts';
import BudgetMeter from '../ui/BudgetMeter.tsx';
import { inheritedText } from './budget.ts';
import LimitField from './LimitField.tsx';

export default function AdminProjects() {
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: ['admin', 'projects'], queryFn: ({ signal }) => listAdminProjects(signal) });
  const settings = useQuery({ queryKey: ['admin', 'settings'], queryFn: ({ signal }) => getAdminSettings(signal) });
  const [ownerFor, setOwnerFor] = useState<string | null>(null);
  const [ownerEmail, setOwnerEmail] = useState('');

  const patch = useMutation({
    mutationFn: ({ id, value }: { id: string; value: ProjectPatch }) => updateAdminProject(id, value),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });
  const owner = useMutation({
    mutationFn: ({ id, email }: { id: string; email: string }) => changeProjectOwner(id, email),
    onSuccess: async () => {
      setOwnerFor(null);
      setOwnerEmail('');
      await queryClient.invalidateQueries({ queryKey: ['admin'] });
    },
  });

  function submitOwner(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    owner.mutate({ id, email: ownerEmail.trim() });
  }

  const defaultProject = settings.data?.default_project_limit_usd ?? null;
  const defaultRun = settings.data?.default_run_limit_usd ?? null;

  return (
    <div>
      <p className="help">Project limits count every run in the project. The run limit applies to each run that has no limit of its own.</p>
      {patch.isError && <p role="alert">{errorText(patch.error)}</p>}
      {owner.isError && <p role="alert">{errorText(owner.error)}</p>}
      {projects.isPending && <p>Loading…</p>}
      {projects.isError && <p role="alert">{errorText(projects.error)}</p>}
      {projects.isSuccess && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Projects</caption>
            <thead>
              <tr>
                <th>Project</th>
                <th>Owner</th>
                <th>Members</th>
                <th>Runs</th>
                <th>Spent</th>
                <th>Project limit</th>
                <th>Run limit</th>
              </tr>
            </thead>
            <tbody>
              {projects.data.map((project) => (
                <tr key={project.id}>
                  <td>
                    <a href={formatRoute({ name: 'project', projectId: project.id })}>{project.name}</a>
                    <small className="help">{project.slug}</small>
                  </td>
                  <td>
                    {project.owner_name ?? '—'}
                    {project.owner_email !== null && <small className="help">{project.owner_email}</small>}
                    <button className="link-button" type="button" onClick={() => { setOwnerFor(project.id); setOwnerEmail(''); }}>Change owner</button>
                    {ownerFor === project.id && (
                      <form className="owner-change" onSubmit={(event) => submitOwner(event, project.id)}>
                        <label className="sr-only" htmlFor={`owner-${project.id}`}>New owner&apos;s email</label>
                        <input
                          id={`owner-${project.id}`}
                          type="email"
                          required
                          placeholder="Email of the new owner"
                          value={ownerEmail}
                          onChange={(event) => setOwnerEmail(event.target.value)}
                        />
                        <button type="submit" disabled={owner.isPending}>Make owner</button>
                        <button type="button" onClick={() => setOwnerFor(null)}>Cancel</button>
                        <p className="help">The current owner stays as a member.</p>
                      </form>
                    )}
                  </td>
                  <td className="num">{project.members}</td>
                  <td className="num">{project.runs}</td>
                  <td><BudgetMeter compact meter={project.budget} label={`Spend of ${project.name}`} /></td>
                  <td>
                    <LimitField
                      label={`Project limit for ${project.name}`}
                      value={project.limit_usd}
                      placeholder={settings.data === undefined ? 'No limit' : inheritedText(defaultProject)}
                      pending={patch.isPending}
                      onSave={(value) => patch.mutate({ id: project.id, value: { limit_usd: value } })}
                    />
                  </td>
                  <td>
                    <LimitField
                      label={`Run limit for runs in ${project.name}`}
                      value={project.run_limit_usd}
                      placeholder={settings.data === undefined ? 'No limit' : inheritedText(defaultRun)}
                      pending={patch.isPending}
                      onSave={(value) => patch.mutate({ id: project.id, value: { run_limit_usd: value } })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
