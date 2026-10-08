import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createProject, getProject, listProjects } from '../api/client.ts';
import type { ProjectSummary } from '../api/types.ts';
import { formatRoute, type Route } from '../routes.ts';
import { errorText, formatTime, isValidSlug, lastActivity, slugify } from '../ui/format.ts';

function ProjectListRow({ project }: { project: ProjectSummary }) {
  const detail = useQuery({
    queryKey: ['project', project.id],
    queryFn: ({ signal }) => getProject(project.id, signal),
  });
  const runs = detail.isSuccess ? String(detail.data.runs.length) : detail.isError ? '—' : '…';
  const activity = detail.isSuccess
    ? formatTime(lastActivity(project.created_at, detail.data.runs))
    : detail.isError ? '—' : '…';
  return (
    <tr>
      <td><a href={formatRoute({ name: 'project', projectId: project.id })}>{project.name}</a></td>
      <td>{project.slug}</td>
      <td>{runs}</td>
      <td>{activity}</td>
    </tr>
  );
}

export default function ProjectsPage({ navigate }: { navigate: (route: Route) => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const projects = useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => listProjects(signal) });
  const create = useMutation({
    mutationFn: () => createProject(slug, name.trim()),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
      navigate({ name: 'project', projectId: created.id });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <>
      <h1>Projects</h1>
      <form className="panel" onSubmit={submit} aria-labelledby="new-project-heading">
        <h2 id="new-project-heading">New project</h2>
        <div className="field">
          <label htmlFor="project-name">Name</label>
          <input
            id="project-name" type="text" required value={name}
            onChange={(event) => {
              setName(event.target.value);
              if (!slugEdited) setSlug(slugify(event.target.value));
            }}
          />
        </div>
        <div className="field">
          <label htmlFor="project-slug">Short name</label>
          <input
            id="project-slug" type="text" value={slug} aria-describedby="project-slug-help"
            onChange={(event) => {
              setSlug(event.target.value);
              setSlugEdited(true);
            }}
          />
          <p className="help" id="project-slug-help">Lowercase letters, digits, - and _. Used for folder names.</p>
        </div>
        {create.isError && <p role="alert">{errorText(create.error)}</p>}
        <div className="actions">
          <button type="submit" className="primary" disabled={create.isPending || !isValidSlug(slug) || name.trim() === ''}>
            Create project
          </button>
        </div>
      </form>
      <h2>Your projects</h2>
      {projects.isPending && <p>Loading projects…</p>}
      {projects.isError && <p role="alert">{errorText(projects.error)}</p>}
      {projects.isSuccess && projects.data.length === 0 && <p>No projects yet. Create one above to upload a dataset.</p>}
      {projects.isSuccess && projects.data.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Your projects</caption>
            <thead><tr><th>Name</th><th>Short name</th><th>Runs</th><th>Last activity</th></tr></thead>
            <tbody>{projects.data.map((project) => <ProjectListRow key={project.id} project={project} />)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
