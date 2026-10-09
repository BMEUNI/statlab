import { useState } from 'react';
import { formatNumber, niceScale, type ChartPanel, type ChartSpec, type XYSeries } from './chart.ts';

const W = 480;
const H = 300;
const PALETTE = ['#1F6FD1', '#E8833A', '#2A9D8F', '#C2352B', '#7B61FF', '#A85F00'];

type Tip = { x: number; y: number; lines: string[] };

function present(values: (number | null)[]): number[] {
  return values.filter((v): v is number => v !== null);
}

function pairs(series: XYSeries): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  series.x.forEach((x, k) => {
    const y = series.y[k];
    if (x !== null && y !== null && y !== undefined) out.push({ x, y });
  });
  return out;
}

export default function Chart({ spec }: { spec: ChartSpec }) {
  return (
    <figure className="chart">
      <figcaption className="chart-title">{spec.title}</figcaption>
      {spec.panels.map((panel, index) => <PanelView panel={panel} key={index} />)}
      {spec.note && <p className="help">{spec.note}</p>}
    </figure>
  );
}

function PanelView({ panel }: { panel: ChartPanel }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const hover = (x: number, y: number, lines: string[]) => ({
    onMouseEnter: () => setTip({ x, y, lines }),
    onMouseLeave: () => setTip(null),
  });

  const xy = panel.kind === 'line' || panel.kind === 'scatter';
  const bands = panel.kind === 'bar' ? panel.categories.length : panel.kind === 'box' ? panel.groups.length : 1;
  const bottom = panel.kind === 'bar' && panel.categories.length > 8 ? 78 : 44;
  const x0 = 52;
  const x1 = W - 12;
  const y0 = H - bottom;
  const y1 = 12;
  const band = (x1 - x0) / bands;

  const xScale = niceScale(
    panel.kind === 'line' || panel.kind === 'scatter' ? present(panel.series.flatMap((s) => s.x)) : [],
    false,
  );
  let yValues: number[];
  if (panel.kind === 'line' || panel.kind === 'scatter') {
    yValues = present(panel.series.flatMap((s) => s.y));
    if (panel.hline !== null) yValues.push(panel.hline);
  } else if (panel.kind === 'bar') {
    yValues = present(panel.series.flatMap((s) => s.values));
  } else {
    yValues = panel.groups.flatMap((g) => [g.low, g.high]);
  }
  const yScale = niceScale(yValues, panel.kind === 'bar');

  const sx = (v: number) => x0 + ((v - xScale.min) / (xScale.max - xScale.min)) * (x1 - x0);
  const sy = (v: number) => y0 - ((v - yScale.min) / (yScale.max - yScale.min)) * (y0 - y1);

  const labels = panel.kind === 'bar' ? panel.categories : panel.kind === 'box' ? panel.groups.map((g) => g.name) : null;
  const rotate = labels !== null && labels.length > 8;

  return (
    <div className="chart-panel">
      {panel.title !== null && <p className="chart-panel-title">{panel.title}</p>}
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={panel.title ?? panel.yLabel}>
        {yScale.ticks.map((t) => (
          <g key={`y${t}`}>
            <line className="chart-grid" x1={x0} x2={x1} y1={sy(t)} y2={sy(t)} />
            <text x={x0 - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{formatNumber(t)}</text>
          </g>
        ))}
        {xy && xScale.ticks.map((t) => (
          <text key={`x${t}`} x={sx(t)} y={y0 + 16} textAnchor="middle">{formatNumber(t)}</text>
        ))}
        {labels !== null && labels.map((label, i) => {
          const cx = x0 + (i + 0.5) * band;
          const text = label.length > 16 ? `${label.slice(0, 16)}…` : label;
          return (
            <text
              key={`b${i}`}
              x={cx}
              y={y0 + 14}
              textAnchor={rotate ? 'end' : 'middle'}
              transform={rotate ? `rotate(-40 ${cx} ${y0 + 14})` : undefined}
            >
              {text}
            </text>
          );
        })}
        <text x={(x0 + x1) / 2} y={H - 6} textAnchor="middle">{panel.xLabel}</text>
        <text x={14} y={(y0 + y1) / 2} textAnchor="middle" transform={`rotate(-90 14 ${(y0 + y1) / 2})`}>{panel.yLabel}</text>
        {panel.kind !== 'bar' && panel.kind !== 'box' && panel.hline !== null && (
          <line className="chart-ref" x1={x0} x2={x1} y1={sy(panel.hline)} y2={sy(panel.hline)} />
        )}
        {panel.kind === 'line' && panel.series.map((s, i) => {
          const color = PALETTE[i % 6];
          const pts = pairs(s);
          return (
            <g key={i}>
              <polyline fill="none" stroke={color} strokeWidth={2} points={pts.map((p) => `${sx(p.x)},${sy(p.y)}`).join(' ')} />
              {pts.map((p, k) => (
                <circle
                  key={k}
                  cx={sx(p.x)}
                  cy={sy(p.y)}
                  r={3}
                  fill={color}
                  {...hover(sx(p.x), sy(p.y), [
                    ...(panel.series.length > 1 ? [s.name] : []),
                    `${panel.xLabel}: ${formatNumber(p.x)}`,
                    `${panel.yLabel}: ${formatNumber(p.y)}`,
                  ])}
                />
              ))}
            </g>
          );
        })}
        {panel.kind === 'scatter' && panel.series.flatMap((s, i) => {
          const color = PALETTE[i % 6];
          return pairs(s).map((p, k) => (
            <circle
              key={`${i}-${k}`}
              cx={sx(p.x)}
              cy={sy(p.y)}
              r={2.5}
              fill={color}
              fillOpacity={0.6}
              {...hover(sx(p.x), sy(p.y), [
                ...(panel.series.length > 1 ? [s.name] : []),
                `${panel.xLabel}: ${formatNumber(p.x)}`,
                `${panel.yLabel}: ${formatNumber(p.y)}`,
              ])}
            />
          ));
        })}
        {panel.kind === 'bar' && panel.categories.flatMap((category, c) => panel.series.map((s, j) => {
          const v = s.values[c];
          if (v === null || v === undefined) return null;
          const w = (band * 0.8) / panel.series.length;
          const bx = x0 + c * band + band * 0.1 + j * w;
          const by = sy(Math.max(v, 0));
          const lines = [
            category,
            panel.series.length > 1 ? `${s.name}: ${formatNumber(v)}` : `${panel.yLabel}: ${formatNumber(v)}`,
          ];
          return (
            <rect
              key={`${c}-${j}`}
              className="mark"
              x={bx}
              y={by}
              width={w}
              height={Math.abs(sy(v) - sy(0))}
              fill={PALETTE[j % 6]}
              {...hover(bx, by, lines)}
            />
          );
        }))}
        {panel.kind === 'box' && panel.groups.map((g, i) => {
          const cx = x0 + (i + 0.5) * band;
          const boxW = band * 0.5;
          const lines = [
            g.name,
            `n = ${g.n}`,
            `median ${formatNumber(g.median)}`,
            `quartiles ${formatNumber(g.q1)} to ${formatNumber(g.q3)}`,
            `whiskers ${formatNumber(g.low)} to ${formatNumber(g.high)}`,
          ];
          return (
            <g key={i}>
              <line x1={cx} x2={cx} y1={sy(g.low)} y2={sy(g.high)} stroke={PALETTE[0]} {...hover(cx, sy(g.high), lines)} />
              <rect
                className="mark"
                x={cx - boxW / 2}
                y={sy(g.q3)}
                width={boxW}
                height={Math.abs(sy(g.q1) - sy(g.q3))}
                fill={PALETTE[0]}
                fillOpacity={0.25}
                stroke={PALETTE[0]}
                {...hover(cx - boxW / 2, sy(g.q3), lines)}
              />
              <line x1={cx - boxW / 2} x2={cx + boxW / 2} y1={sy(g.median)} y2={sy(g.median)} stroke={PALETTE[0]} strokeWidth={2.5} pointerEvents="none" />
            </g>
          );
        })}
      </svg>
      {tip !== null && (
        <div className="chart-tip" style={{ left: `${(tip.x / W) * 100}%`, top: `${(tip.y / H) * 100}%` }}>
          {tip.lines.map((line, i) => <div key={i}>{line}</div>)}
        </div>
      )}
      {panel.kind !== 'box' && panel.series.length > 1 && (
        <ul className="chart-legend">
          {panel.series.map((s, i) => (
            <li key={i}><span className="swatch" style={{ background: PALETTE[i % 6] }} />{s.name}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
