import { useEffect, useState } from 'react';
import { formatRoute, parseRoute, type Route } from './routes.ts';

export function useRoute(): { route: Route; navigate: (route: Route) => void } {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const navigate = (next: Route) => {
    window.location.hash = formatRoute(next);
  };
  return { route, navigate };
}
