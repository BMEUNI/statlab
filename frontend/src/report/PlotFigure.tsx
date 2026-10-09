import { useQuery } from '@tanstack/react-query';
import { getRunChart } from '../api/client.ts';
import Chart from './Chart.tsx';
import { chartRef, parseChart } from './chart.ts';

export default function PlotFigure({ runId, alt, src, url }: { runId: string; alt: string; src: string; url: string }) {
  const ref = chartRef(src);
  const query = useQuery({
    queryKey: ['chart', runId, ref],
    queryFn: ({ signal }) => getRunChart(runId, ref ?? '', signal),
    enabled: ref !== null,
    retry: false,
    staleTime: Infinity,
  });
  const spec = query.isSuccess ? parseChart(query.data) : null;
  if (spec !== null) {
    return (
      <div className="plot-figure">
        <Chart spec={spec} />
        <a className="chart-open" href={url} target="_blank" rel="noreferrer noopener">Open image</a>
      </div>
    );
  }
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" title="Open full size">
      <img className="plot" alt={alt} src={url} />
    </a>
  );
}
