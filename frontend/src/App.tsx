import { useQuery } from '@tanstack/react-query';
import { getProject } from './api/client.ts';
import ProjectPage from './pages/ProjectPage.tsx';
import ProjectsPage from './pages/ProjectsPage.tsx';
import RunPage from './pages/RunPage.tsx';
import { formatRoute } from './routes.ts';
import SetupWizard from './setup/SetupWizard.tsx';
import { useRoute } from './useRoute.ts';

export default function App() {
  const { route, navigate } = useRoute();
  const projectId = route.name === 'project' || route.name === 'setup' ? route.projectId : null;
  const cached = useQuery({
    queryKey: ['project', projectId],
    enabled: false,
    queryFn: ({ signal }) => projectId === null ? Promise.reject(new Error('Project ID is required.')) : getProject(projectId, signal),
  });
  const projectName = cached.data?.project.name ?? projectId;

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">Statlab</a>
        <nav className="breadcrumb" aria-label="Breadcrumb">
          Projects
          {projectId !== null && (
            <>
              <span>›</span>
              <a href={formatRoute({ name: 'project', projectId })}>{projectName}</a>
            </>
          )}
          {route.name === 'setup' && (<><span>›</span>New run</>)}
          {route.name === 'run' && (<><span>›</span>Run {route.runId}</>)}
        </nav>
      </header>
      <main className="page">
        {route.name === 'projects' && <ProjectsPage navigate={navigate} />}
        {route.name === 'project' && <ProjectPage projectId={route.projectId} navigate={navigate} />}
        {route.name === 'setup' && <SetupWizard projectId={route.projectId} navigate={navigate} />}
        {route.name === 'run' && <RunPage runId={route.runId} />}
        {route.name === 'notFound' && (
          <>
            <h1>Page not found</h1>
            <p><a href="#/">Go to projects</a></p>
          </>
        )}
      </main>
    </>
  );
}
