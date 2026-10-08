import { isJsonObject, publicCandidateId } from '../api/client.ts';
import { fieldsOf } from '../contract/schema.ts';
import type {
  ExecutionRecord,
  GateAction,
  GatePhase,
  GateQuestion,
  GateSummary,
  JsonObject,
  JsonValue,
  LlmCallRecord,
  RunEvent,
  RunExecutions,
  RunMode,
  RunStatus,
  RunSummary,
  ToolCallRecord,
  Vocabulary,
} from '../api/types.ts';

export const GATE_ACTIONS: GateAction[] = [
  'approve',
  'approve_with_edits',
  'instruct',
  'approve_all_remaining',
  'skip_node',
  'rewind',
  'abort',
];

export const ACTION_LABELS: Record<GateAction, string> = {
  approve: 'Approve',
  approve_with_edits: 'Approve with my edits',
  instruct: 'Ask for a revision',
  approve_all_remaining: 'Approve and run the rest automatically',
  skip_node: 'Skip this step',
  rewind: 'Rewind to an earlier step',
  abort: 'Stop the run',
};

export function humanize(text: string): string {
  const spaced = text.replaceAll('_', ' ').replace(/ +/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function nodeLabel(name: string): string {
  return humanize(name);
}

export function forkNote(run: RunSummary): string | null {
  if (run.forked_from_run_id === null) return null;
  let config: unknown;
  try {
    config = JSON.parse(run.config_json);
  } catch {
    return null;
  }
  if (!isJsonObject(config) || !isJsonObject(config.fork) || typeof config.fork.target !== 'string') return null;
  const note = `This run continues from run ${run.forked_from_run_id} and starts again at ${nodeLabel(config.fork.target)}; earlier steps are reused.`;
  const edits = config.fork.edits;
  if (!isJsonObject(edits)) return note;
  const entries = Object.entries(edits);
  if (entries.length === 0) return note;
  return `${note} Changed settings: ${entries.map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join(', ')}.`;
}

export function switchedToAutomatic(gates: GateSummary[]): boolean {
  return gates.some((gate) => {
    if (gate.answer_json === null) return false;
    try {
      const answer: unknown = JSON.parse(gate.answer_json);
      return isJsonObject(answer) && answer.action === 'approve_all_remaining';
    } catch {
      return false;
    }
  });
}

export function modeLabel(mode: RunMode, switched: boolean): string {
  if (switched) return 'Guided, then automatic';
  return mode === 'guided' ? 'Guided' : 'Autonomous';
}

export const STEP_SHORT_LABELS: Record<string, string> = {
  preprocessing: 'Clean',
  feature_engineering: 'Features',
  model_training: 'Train',
  model_evaluation: 'Evaluate',
  model_interpretation: 'Explain',
};

export function stepShortLabel(node: string): string {
  return STEP_SHORT_LABELS[node] ?? nodeLabel(node);
}

export const ESTIMATOR_LABELS: Record<string, string> = {
  logistic_regression: 'Logistic regression',
  random_forest_classifier: 'Random forest',
  hist_gb_classifier: 'Gradient boosting',
  linear_regression: 'Linear regression',
  ridge: 'Ridge regression',
  random_forest_regressor: 'Random forest',
  hist_gb_regressor: 'Gradient boosting',
};

export function estimatorLabel(name: string | null): string {
  if (name === null) return 'Model';
  return ESTIMATOR_LABELS[name] ?? humanize(name);
}

export const METRIC_LABELS: Record<string, string> = {
  accuracy: 'Accuracy',
  balanced_accuracy: 'Balanced accuracy',
  precision: 'Precision',
  recall: 'Recall',
  f1: 'F1',
  roc_auc: 'ROC AUC',
  pr_auc: 'PR AUC',
  brier: 'Brier score',
  log_loss: 'Log loss',
  rmse: 'RMSE',
  mae: 'MAE',
  r2: 'R²',
  medae: 'Median absolute error',
  mape: 'MAPE',
};

export function metricLabel(name: string): string {
  return METRIC_LABELS[name] ?? humanize(name);
}

function isAction(value: unknown): value is GateAction {
  return typeof value === 'string' && (GATE_ACTIONS as string[]).includes(value);
}

function isPhase(value: unknown): value is GatePhase {
  return value === 'before' || value === 'after' || value === 'failure';
}

function isStrings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

export function parseGateQuestion(text: string): GateQuestion | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isJsonObject(value)) return null;
  const { node, phase, proposal, decision_schema: schema, evidence, reasoning, editable } = value;
  const { options, context, revision, history, candidate_id: candidate, failure } = value;
  const defaultAction = value.default;
  if (
    typeof node !== 'string' || !isPhase(phase) || !isJsonObject(proposal) || !isJsonObject(schema) ||
    !Array.isArray(evidence) || typeof reasoning !== 'string' || !isStrings(editable) ||
    !isAction(defaultAction) || !Array.isArray(options) || !options.every(isAction) || !isStrings(context) ||
    typeof revision !== 'number' || !Number.isInteger(revision) || revision < 0 || !Array.isArray(history) ||
    (candidate !== null && typeof candidate !== 'string')
  ) return null;
  let parsedFailure: GateQuestion['failure'] = null;
  if (failure !== null) {
    if (!isJsonObject(failure) || typeof failure.error_class !== 'string' || typeof failure.message !== 'string') {
      return null;
    }
    parsedFailure = { errorClass: failure.error_class, message: failure.message };
  }
  return {
    node,
    candidateId: publicCandidateId(candidate ?? null),
    phase,
    proposal,
    decisionSchema: schema,
    evidence,
    reasoning,
    editable,
    defaultAction,
    options,
    context,
    revision,
    history,
    failure: parsedFailure,
  };
}

export type SpineNode = { name: string; kind: 'SHARED' | 'PER_CANDIDATE'; skippable: boolean };
export type NodeOutcome = {
  node: string;
  executionId: string;
  status: string;
  summary: JsonObject;
  artifacts: string[];
  metrics: Record<string, number>;
  warnings: string[];
  skipReason: string | null;
  error: { errorClass: string; message: string } | null;
};
export type Branch = {
  id: string;
  label: string;
  estimator: string | null;
  status: string;
  wave: number;
  retuneCount: number;
  outcomes: NodeOutcome[];
  spec: JsonObject;
};
export type RunView = {
  spine: SpineNode[];
  shared: NodeOutcome[];
  branches: Branch[];
  overlap: string | null;
  wave: number;
  budget: { usedMs: number; limitMs: number } | null;
};

function outcome(entry: JsonValue): NodeOutcome | null {
  if (!isJsonObject(entry)) return null;
  const result = entry.result;
  if (typeof entry.node_name !== 'string' || typeof entry.execution_id !== 'string') return null;
  if (!isJsonObject(result) || typeof result.status !== 'string') return null;
  const metrics: Record<string, number> = {};
  if (isJsonObject(result.metrics)) {
    for (const [key, item] of Object.entries(result.metrics)) {
      if (typeof item === 'number' && Number.isFinite(item)) metrics[key] = item;
    }
  }
  const error = result.error;
  return {
    node: entry.node_name,
    executionId: entry.execution_id,
    status: result.status,
    summary: isJsonObject(result.summary) ? result.summary : {},
    artifacts: Array.isArray(result.artifacts) ? result.artifacts.filter((item) => typeof item === 'string') : [],
    metrics,
    warnings: Array.isArray(result.warnings) ? result.warnings.filter((item) => typeof item === 'string') : [],
    skipReason: typeof result.skip_reason === 'string' ? result.skip_reason : null,
    error: isJsonObject(error) && typeof error.error_class === 'string' && typeof error.message === 'string'
      ? { errorClass: error.error_class, message: error.message }
      : null,
  };
}

function outcomes(value: JsonValue | undefined): NodeOutcome[] {
  if (!Array.isArray(value)) return [];
  return value.map(outcome).filter((item): item is NodeOutcome => item !== null);
}

function branch(entry: JsonValue): Branch | null {
  if (!isJsonObject(entry) || typeof entry.public_id !== 'string') return null;
  const spec = isJsonObject(entry.spec) ? entry.spec : {};
  const estimator = isJsonObject(spec.estimator) && typeof spec.estimator.name === 'string'
    ? spec.estimator.name
    : null;
  return {
    id: entry.public_id,
    label: typeof spec.label === 'string' ? spec.label : entry.public_id,
    estimator,
    status: typeof entry.status === 'string' ? entry.status : 'pending',
    wave: typeof entry.wave === 'number' ? entry.wave : 1,
    retuneCount: typeof entry.retune_count === 'number' ? entry.retune_count : 0,
    outcomes: outcomes(entry.results),
    spec,
  };
}

export function parseRunView(state: JsonObject): RunView | null {
  if (!Array.isArray(state.spine)) return null;
  const spine: SpineNode[] = [];
  for (const entry of state.spine) {
    if (
      !isJsonObject(entry) || typeof entry.name !== 'string' || typeof entry.skippable !== 'boolean' ||
      (entry.kind !== 'SHARED' && entry.kind !== 'PER_CANDIDATE')
    ) return null;
    spine.push({ name: entry.name, kind: entry.kind, skippable: entry.skippable });
  }
  const branches = Array.isArray(state.branches)
    ? state.branches.map(branch).filter((item): item is Branch => item !== null)
    : [];
  const traversal = state.traversal;
  const budget = state.budget;
  return {
    spine,
    shared: outcomes(state.shared_results),
    branches,
    overlap: isJsonObject(traversal) && typeof traversal.overlap_shared === 'string' ? traversal.overlap_shared : null,
    wave: typeof state.wave === 'number' ? state.wave : 1,
    budget: isJsonObject(budget) && typeof budget.total_fit_ms === 'number' && typeof budget.limit_ms === 'number'
      ? { usedMs: budget.total_fit_ms, limitMs: budget.limit_ms }
      : null,
  };
}

export type GraphLayout = { before: SpineNode[]; perCandidate: SpineNode[]; after: SpineNode[] };

export function graphLayout(spine: SpineNode[]): GraphLayout {
  const first = spine.findIndex((node) => node.kind === 'PER_CANDIDATE');
  if (first < 0) return { before: spine, perCandidate: [], after: [] };
  const last = spine.map((node) => node.kind).lastIndexOf('PER_CANDIDATE');
  return {
    before: spine.slice(0, first),
    perCandidate: spine.slice(first, last + 1).filter((node) => node.kind === 'PER_CANDIDATE'),
    after: spine.slice(last + 1),
  };
}

export type CellStatus = 'pending' | 'running' | 'awaiting' | 'completed' | 'skipped' | 'failed';

export function cellStatus(
  node: string,
  candidateId: string | null,
  executions: ExecutionRecord[],
  pending: GateQuestion[],
  outcome: NodeOutcome | null,
): CellStatus {
  if (pending.some((question) => question.node === node && question.candidateId === candidateId)) return 'awaiting';
  let latest: ExecutionRecord | null = null;
  for (const execution of executions) {
    if (execution.node === node && execution.candidateId === candidateId && (latest === null || execution.seq > latest.seq ||
      (execution.seq === latest.seq && execution.attempt > latest.attempt))) {
      latest = execution;
    }
  }
  if (latest === null) {
    const done = outcome?.status;
    if (done === 'completed' || done === 'skipped' || done === 'failed') return done;
    return done === 'awaiting_user' ? 'awaiting' : 'pending';
  }
  const status = latest.status;
  if (status === 'pending' || status === 'running') return 'running';
  if (status === 'awaiting_user') return 'awaiting';
  if (status === 'completed' || status === 'skipped' || status === 'failed') return status;
  return 'pending';
}

export function executionsFor(
  node: string,
  candidateId: string | null,
  data: RunExecutions,
): { execution: ExecutionRecord; toolCalls: ToolCallRecord[]; llmCalls: LlmCallRecord[] }[] {
  return data.executions
    .filter((execution) => execution.node === node && execution.candidateId === candidateId)
    .sort((a, b) => b.seq - a.seq || b.attempt - a.attempt)
    .map((execution) => ({
      execution,
      toolCalls: data.toolCalls.filter((call) => call.executionId === execution.id),
      llmCalls: data.llmCalls.filter((call) => call.executionId === execution.id),
    }));
}

export function gateEdits(proposal: JsonObject, draft: JsonObject, editable: string[]): JsonObject {
  const edits: JsonObject = {};
  for (const key of editable) {
    const value = draft[key];
    if (value !== undefined && JSON.stringify(value) !== JSON.stringify(proposal[key])) edits[key] = value;
  }
  return edits;
}

export function gateFieldSplit(question: GateQuestion): { main: string[]; rest: string[] } {
  const fields = fieldsOf(question.decisionSchema, null);
  const editable = new Set(question.editable);
  const requiredList = question.decisionSchema.required;
  const required = new Set(Array.isArray(requiredList) ? requiredList.filter((item): item is string => typeof item === 'string') : []);
  const usable = fields.filter((field) => editable.has(field.key) && field.key !== 'user_summary');
  const isSet = (key: string, defaultValue: JsonValue | undefined): boolean => {
    const value = question.proposal[key];
    if (value === undefined || value === null) return false;
    if (Array.isArray(value) && value.length === 0) return false;
    if (isJsonObject(value) && Object.keys(value).length === 0) return false;
    if (typeof value === 'string' && value.trim() === '') return false;
    return JSON.stringify(value) !== JSON.stringify(defaultValue);
  };
  const main = usable
    .filter((field) => field.key !== 'rationale' && (required.has(field.key) || isSet(field.key, field.defaultValue)))
    .slice(0, 6)
    .map((field) => field.key);
  return { main, rest: usable.map((field) => field.key).filter((key) => !main.includes(key)) };
}

export function availableActions(question: GateQuestion, skippable: boolean): GateAction[] {
  return GATE_ACTIONS.filter((action) => {
    if (!question.options.includes(action)) return false;
    if ((action === 'approve_with_edits' || action === 'instruct') && question.phase !== 'before') return false;
    if (action === 'approve_with_edits' && question.editable.length === 0) return false;
    return action !== 'skip_node' || skippable;
  });
}

export type FeedItem =
  | { kind: 'user'; seq: number; ts: string; messageId: string; text: string }
  | { kind: 'assistant'; seq: number; ts: string; replyTo: string; text: string; category: string | null; newRunId: string | null }
  | { kind: 'error'; seq: number; ts: string; replyTo: string; text: string }
  | { kind: 'activity'; seq: number; ts: string; text: string }
  | {
      kind: 'step';
      seq: number;
      ts: string;
      node: string;
      candidateId: string | null;
      status: 'completed' | 'failed' | 'skipped';
      text: string;
    }
  | { kind: 'fork'; seq: number; ts: string; newRunId: string };
export type RunFeed = { items: FeedItem[]; drafts: Record<string, string>; completedSeq: number | null };

export function firstSentences(text: string, n: number): string {
  return text.trim().split(/(?<=[.!?])\s+/).slice(0, n).join(' ');
}

export function narrate(
  summary: JsonObject,
  status: string,
  skipReason: string | null,
  errorMessage: string | null,
): string {
  if (status === 'failed') return errorMessage ? `It failed: ${errorMessage}` : 'It failed.';
  if (status === 'skipped') return skipReason ? `Skipped: ${skipReason}` : 'Skipped.';
  const { decision } = summary;
  if (isJsonObject(decision)) {
    const { user_summary: userSummary, rationale } = decision;
    if (typeof userSummary === 'string' && userSummary.trim() !== '') return userSummary.trim();
    if (typeof rationale === 'string' && rationale.trim() !== '') return firstSentences(rationale, 2);
  }
  return 'Done.';
}

export function narrateOutcome(outcome: NodeOutcome): string {
  return narrate(outcome.summary, outcome.status, outcome.skipReason, outcome.error?.message ?? null);
}

function where(node: string, candidate: JsonValue | undefined): string {
  return typeof candidate === 'string' ? `${nodeLabel(node)} (${candidate})` : nodeLabel(node);
}

export function foldEvents(events: RunEvent[]): RunFeed {
  const items: FeedItem[] = [];
  const drafts: Record<string, string> = {};
  const answered = new Set<string>();
  const forks = new Set<string>();
  let completedSeq: number | null = null;
  for (const event of events) {
    const { seq, ts, payload: p } = event;
    const base = { seq, ts };
    switch (event.type) {
      case 'message.user':
        if (typeof p.message_id === 'string' && typeof p.text === 'string') {
          items.push({ ...base, kind: 'user', messageId: p.message_id, text: p.text });
        }
        break;
      case 'message.assistant.delta':
        if (typeof p.reply_to === 'string' && typeof p.text === 'string' && typeof p.reset === 'boolean') {
          drafts[p.reply_to] = p.reset ? p.text : (drafts[p.reply_to] ?? '') + p.text;
        }
        break;
      case 'message.assistant.complete':
        if (typeof p.reply_to === 'string' && typeof p.text === 'string') {
          delete drafts[p.reply_to];
          const response = isJsonObject(p.response) ? p.response : {};
          items.push({
            ...base,
            kind: 'assistant',
            replyTo: p.reply_to,
            text: p.text,
            category: typeof response.category === 'string' ? response.category : null,
            newRunId: typeof response.new_run_id === 'string' ? response.new_run_id : null,
          });
        }
        break;
      case 'error':
        if (typeof p.reply_to === 'string' && typeof p.message === 'string') {
          delete drafts[p.reply_to];
          items.push({ ...base, kind: 'error', replyTo: p.reply_to, text: p.message });
        }
        break;
      case 'node.started':
      case 'node.completed':
      case 'node.skipped':
      case 'node.failed':
        if (typeof p.node === 'string') {
          items.push({ ...base, kind: 'activity', text: `${where(p.node, p.candidate_id)} ${event.type.slice(5)}` });
          if (event.type !== 'node.started') {
            const candidate = typeof p.candidate_id === 'string' ? publicCandidateId(p.candidate_id) : null;
            const result = isJsonObject(p.result) ? p.result : {};
            if (candidate === null || event.type === 'node.failed' ||
              (event.type === 'node.completed' && p.node === 'model_evaluation')) {
              const status = event.type.slice(5) as 'completed' | 'failed' | 'skipped';
              const error = isJsonObject(result.error) ? result.error : {};
              items.push({
                ...base,
                kind: 'step',
                node: p.node,
                candidateId: candidate,
                status,
                text: narrate(
                  isJsonObject(result.summary) ? result.summary : {},
                  status,
                  typeof result.skip_reason === 'string' ? result.skip_reason : null,
                  typeof error.message === 'string' ? error.message : null,
                ),
              });
            }
          }
        }
        break;
      case 'gate.opened': {
        const question = p.question;
        if (isJsonObject(question) && typeof question.node === 'string') {
          const candidate = typeof question.candidate_id === 'string'
            ? publicCandidateId(question.candidate_id)
            : undefined;
          items.push({
            ...base,
            kind: 'activity',
            text: `Waiting for your decision at ${where(question.node, candidate)}`,
          });
        }
        break;
      }
      case 'gate.revised':
        items.push({ ...base, kind: 'activity', text: 'The agent revised its proposal' });
        break;
      case 'gate.answered':
        if (isJsonObject(p.answer) && isAction(p.answer.action)) {
          if (typeof p.gate_id !== 'string' || typeof p.answer.revision !== 'number' || !Number.isInteger(p.answer.revision)) break;
          const key = `${p.gate_id}:${p.answer.revision}`;
          if (answered.has(key)) break;
          answered.add(key);
          items.push({ ...base, kind: 'activity', text: `You chose: ${ACTION_LABELS[p.answer.action]}` });
        }
        break;
      case 'gate.closed':
      case 'run.forked':
        if (typeof p.new_run_id === 'string' && !forks.has(p.new_run_id)) {
          forks.add(p.new_run_id);
          items.push({ ...base, kind: 'fork', newRunId: p.new_run_id });
        }
        break;
      case 'run.status':
        if (typeof p.status === 'string') {
          items.push({ ...base, kind: 'activity', text: `Run ${p.status.replace('_', ' ')}` });
          if (p.status === 'completed') completedSeq = seq;
        }
        break;
      default:
        break;
    }
  }
  return { items, drafts, completedSeq };
}

export const STATUS_LABELS: Record<RunStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  awaiting_user: 'Waiting for you',
  paused: 'Paused',
  failed: 'Failed',
  cancelled: 'Stopped',
  completed: 'Completed',
  superseded: 'Replaced by a rewind',
};

export function statusTone(status: RunStatus): 'signal' | 'gate' | 'fault' | 'neutral' {
  if (status === 'running' || status === 'completed') return 'signal';
  if (status === 'awaiting_user' || status === 'paused') return 'gate';
  if (status === 'failed' || status === 'cancelled') return 'fault';
  return 'neutral';
}

export const CELL_LABELS: Record<CellStatus, string> = {
  pending: 'Not started',
  running: 'Running',
  awaiting: 'Waiting for you',
  completed: 'Done',
  skipped: 'Skipped',
  failed: 'Failed',
};

export const PHASE_LABELS: Record<GatePhase, string> = {
  before: 'Before it runs',
  after: 'After it ran',
  failure: 'It failed',
};

export function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function runControls(
  status: RunStatus,
): { pause: boolean; resume: boolean; cancel: boolean; rewind: boolean } {
  return {
    pause: status === 'pending' || status === 'running',
    resume: status === 'paused',
    cancel: ['pending', 'running', 'paused', 'awaiting_user', 'failed'].includes(status),
    rewind: status !== 'pending' && status !== 'superseded',
  };
}

export function datasetColumns(view: RunView | null): string[] {
  if (view === null) return [];
  const tools = latestOutcome(view, 'intake', null)?.summary.tools;
  if (!Array.isArray(tools)) return [];
  const load = tools.find((entry) => isJsonObject(entry) && entry.tool === 'load_csv');
  if (!isJsonObject(load) || !isJsonObject(load.result) || !isJsonObject(load.result.summary)) return [];
  const { columns } = load.result.summary;
  return Array.isArray(columns) ? columns.filter((item): item is string => typeof item === 'string') : [];
}

export function latestOutcome(view: RunView, node: string, candidateId: string | null): NodeOutcome | null {
  const list = candidateId === null
    ? view.shared
    : view.branches.find((item) => item.id === candidateId)?.outcomes ?? [];
  return list.filter((item) => item.node === node).at(-1) ?? null;
}

export const EMPTY_VOCABULARY: Vocabulary = { contractSchema: {}, estimators: [], inferentialModels: [] };

export const TONES: Record<CellStatus, string> = {
  pending: 'upcoming',
  running: 'current',
  awaiting: 'awaiting',
  completed: 'done',
  skipped: 'skipped',
  failed: 'fault',
};

export function groupTone(statuses: CellStatus[]): string {
  if (statuses.length === 0) return 'upcoming';
  if (statuses.includes('failed')) return 'fault';
  if (statuses.includes('awaiting')) return 'awaiting';
  if (statuses.includes('running')) return 'current';
  if (statuses.every((status) => status === 'completed' || status === 'skipped')) return 'done';
  if (statuses.some((status) => status === 'completed' || status === 'skipped')) return 'current';
  return 'upcoming';
}

export const DESIGN_NOTE = "Approving this also approves every model's cleaning, training, evaluation and explanation; they only stop to ask if a step fails.";

export const STEP_PURPOSES: Record<string, string> = {
  preprocessing: 'Clean the data for each model: fill missing values and drop unusable columns.',
  feature_engineering: "Build each model's inputs: encode categories and scale numbers.",
  model_training: 'Train each model on the training rows.',
  model_evaluation: 'Score each model on the validation rows.',
  model_interpretation: 'Check which inputs each model relies on and where it makes mistakes.',
};

export function gateNotes(question: GateQuestion): string[] {
  const notes: string[] = [];
  const exceptions = question.proposal.material_exceptions;
  if (Array.isArray(exceptions)) notes.push(...exceptions.filter((item): item is string => typeof item === 'string'));
  for (const entry of question.evidence) {
    if (isJsonObject(entry) && Array.isArray(entry.warnings)) {
      notes.push(...entry.warnings.filter((item): item is string => typeof item === 'string'));
    }
  }
  return [...new Set(notes)];
}

export type DecisionItem = { gate: GateSummary; question: GateQuestion };
export type DecisionGroup = {
  key: string;
  node: string;
  phase: GatePhase;
  candidate: boolean;
  items: DecisionItem[];
  notes: string[] | null;
};

export function groupDecisions(items: DecisionItem[]): DecisionGroup[] {
  const sorted = [...items].sort((a, b) => (a.gate.ts < b.gate.ts ? -1 : a.gate.ts > b.gate.ts ? 1 : 0));
  const groups = new Map<string, DecisionGroup>();
  for (const item of sorted) {
    const { question } = item;
    const key = question.candidateId === null ? `gate:${item.gate.id}` : `${question.node}:${question.phase}`;
    const existing = groups.get(key);
    if (existing === undefined) {
      groups.set(key, {
        key,
        node: question.node,
        phase: question.phase,
        candidate: question.candidateId !== null,
        items: [item],
        notes: null,
      });
    } else {
      existing.items.push(item);
    }
  }
  return [...groups.values()].map((group) => {
    const all = group.items.map((item) => gateNotes(item.question));
    const first = all[0] ?? [];
    const same = all.every((notes) => JSON.stringify(notes) === JSON.stringify(first));
    return { ...group, notes: same ? first : null };
  });
}

export function decisionLead(group: DecisionGroup): string {
  const first = group.items[0];
  if (first === undefined) return '';
  const { question: q } = first;
  if (q.phase === 'failure') return `This step failed: ${q.failure?.message ?? 'unknown error'}`;
  if (group.candidate) {
    const purpose = STEP_PURPOSES[group.node] ?? `${nodeLabel(group.node)}.`;
    return q.phase === 'before'
      ? `${purpose} I noted points to check before I start.`
      : `${purpose} Done; I noted points to check.`;
  }
  const summary = q.proposal.user_summary;
  const lead = typeof summary === 'string' && summary.trim() !== '' ? firstSentences(summary, 2) : firstSentences(q.reasoning, 2);
  return group.node === 'experiment_design' ? `${lead} ${DESIGN_NOTE}` : lead;
}

export function nowLine(
  status: RunStatus,
  view: RunView | null,
  executions: ExecutionRecord[],
  pending: GateQuestion[],
): string {
  if (pending.length > 0) {
    const counts = new Map<string, number>();
    for (const question of pending) {
      counts.set(question.node, (counts.get(question.node) ?? 0) + (question.candidateId === null ? 0 : 1));
    }
    const parts = [...counts].map(([node, n]) => (n > 1 ? `${nodeLabel(node)} (${n} models)` : nodeLabel(node)));
    return `Waiting for your decision: ${parts.join(', ')}`;
  }
  if ((status === 'running' || status === 'pending') && view !== null) {
    const parts: string[] = [];
    for (const node of view.spine) {
      const ids: (string | null)[] = node.kind === 'SHARED' ? [null] : view.branches.map((branch) => branch.id);
      const n = ids.filter((id) =>
        cellStatus(node.name, id, executions, [], latestOutcome(view, node.name, id)) === 'running').length;
      if (n > 0) {
        parts.push(node.kind === 'SHARED' ? nodeLabel(node.name) : `${nodeLabel(node.name)} (${n} model${n === 1 ? '' : 's'})`);
      }
    }
    return parts.length > 0 ? `Working on: ${parts.join(', ')}` : 'Starting the next step…';
  }
  return STATUS_LABELS[status];
}

export function jsonBlock(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch {
    return null;
  }
  return Array.isArray(value) || isJsonObject(value) ? JSON.stringify(value, null, 2) : null;
}

export function splitLead(text: string, n = 1): { lead: string; more: string | null } {
  const lead = firstSentences(text, n);
  const rest = text.trim().slice(lead.length).trim();
  return { lead, more: rest === '' ? null : rest };
}
