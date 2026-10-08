export type Route =
  | { name: 'projects' }
  | { name: 'project'; projectId: string }
  | { name: 'setup'; projectId: string }
  | { name: 'run'; runId: string }
  | { name: 'notFound' };

const NOT_FOUND: Route = { name: 'notFound' };

export function parseRoute(hash: string): Route {
  let text = hash.startsWith('#') ? hash.slice(1) : hash;
  if (text.startsWith('/')) text = text.slice(1);
  const segments = text.split('/');
  if (segments.length === 1 && segments[0] === '') return { name: 'projects' };
  let ids: string[];
  try {
    ids = segments.map((segment) => decodeURIComponent(segment));
  } catch {
    return NOT_FOUND;
  }
  const [head, id, tail] = ids;
  if (id === undefined || id === '') return NOT_FOUND;
  if (head === 'projects' && ids.length === 2) return { name: 'project', projectId: id };
  if (head === 'projects' && ids.length === 3 && tail === 'new') return { name: 'setup', projectId: id };
  if (head === 'runs' && ids.length === 2) return { name: 'run', runId: id };
  return NOT_FOUND;
}

export function formatRoute(route: Route): string {
  switch (route.name) {
    case 'project': return `#/projects/${encodeURIComponent(route.projectId)}`;
    case 'setup': return `#/projects/${encodeURIComponent(route.projectId)}/new`;
    case 'run': return `#/runs/${encodeURIComponent(route.runId)}`;
    default: return '#/';
  }
}
