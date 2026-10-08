import ChatHome from './chat/ChatHome.tsx';
import { CURRENT_KEY, browserStore, clearSession } from './chat/session.ts';
import ProjectPage from './pages/ProjectPage.tsx';
import ProjectsPage from './pages/ProjectsPage.tsx';
import RunPage from './pages/RunPage.tsx';
import SetupWizard from './setup/SetupWizard.tsx';
import { useState } from 'react';
import { useRoute } from './useRoute.ts';

export default function App() {
  const { route, navigate } = useRoute();
  const [chatKey, setChatKey] = useState(0);
  const inProjects = route.name === 'projects' || route.name === 'project' || route.name === 'setup';

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/"><span className="brand-mark" aria-hidden="true" />Statlab</a>
        <nav className="topnav" aria-label="Main">
          <button
            type="button"
            onClick={() => {
              clearSession(browserStore(), CURRENT_KEY);
              setChatKey((key) => key + 1);
              navigate({ name: 'home' });
            }}
          >New chat</button>
          <a className="button" href="#/projects" aria-current={inProjects ? 'page' : undefined}>Projects &amp; runs</a>
        </nav>
      </header>
      <main className={route.name === 'home' || route.name === 'run' ? 'app-main' : 'app-main scroll'}>
        {route.name === 'home' && <ChatHome key={chatKey} navigate={navigate} />}
        {route.name === 'run' && <RunPage key={route.runId} runId={route.runId} navigate={navigate} />}
        {route.name === 'projects' && <div className="page"><ProjectsPage navigate={navigate} /></div>}
        {route.name === 'project' && <div className="page"><ProjectPage projectId={route.projectId} navigate={navigate} /></div>}
        {route.name === 'setup' && <div className="page"><SetupWizard projectId={route.projectId} navigate={navigate} /></div>}
        {route.name === 'notFound' && (
          <div className="page">
            <h1>Page not found</h1>
            <p><a href="#/">Go to the chat</a></p>
          </div>
        )}
      </main>
    </div>
  );
}
