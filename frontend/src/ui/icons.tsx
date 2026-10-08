import {
  Archive, ChartColumn, CircleCheck, CircleDashed, CircleDot, CircleX, Cpu, Eraser, FileInput, FlaskConical,
  Gauge, Hand, Lightbulb, LoaderCircle, Shapes, Sigma, SkipForward, Split, Target, Trophy,
  type LucideIcon,
} from 'lucide-react';
import type { CellStatus } from '../run/model.ts';

export const NODE_ICONS: Record<string, LucideIcon> = {
  intake: FileInput,
  problem_definition: Target,
  data_profiling: ChartColumn,
  data_splitting: Split,
  experiment_design: FlaskConical,
  inferential_analysis: Sigma,
  preprocessing: Eraser,
  feature_engineering: Shapes,
  model_training: Cpu,
  model_evaluation: Gauge,
  model_interpretation: Lightbulb,
  champion_selection: Trophy,
  artifact_persistence: Archive,
};

export function NodeIcon({ node }: { node: string }) {
  const Icon = NODE_ICONS[node] ?? CircleDot;
  return <Icon className="icon" aria-hidden="true" />;
}

export const STATUS_ICONS: Record<CellStatus, LucideIcon> = {
  pending: CircleDashed,
  running: LoaderCircle,
  awaiting: Hand,
  completed: CircleCheck,
  skipped: SkipForward,
  failed: CircleX,
};

export function StatusIcon({ status }: { status: CellStatus }) {
  const Icon = STATUS_ICONS[status];
  return <Icon className={`icon status-icon icon-${status}`} aria-hidden="true" />;
}
