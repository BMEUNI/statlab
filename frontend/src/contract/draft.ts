import { isJsonObject } from '../api/client.ts';
import type { DatasetProfile, JsonObject, JsonValue } from '../api/types.ts';

export type Path = readonly (string | number)[];

export function getAt(value: JsonValue | undefined, path: Path): JsonValue | undefined {
  let current = value;
  for (const key of path) {
    if (typeof key === 'string' && isJsonObject(current)) current = current[key];
    else if (typeof key === 'number' && Array.isArray(current)) current = current[key];
    else return undefined;
  }
  return current;
}

function setIn(node: JsonValue | undefined, path: Path, value: JsonValue): JsonValue {
  const [key, ...rest] = path;
  if (key === undefined) return value;
  if (typeof key === 'number') {
    const copy = Array.isArray(node) ? [...node] : [];
    copy[key] = setIn(copy[key], rest, value);
    return copy;
  }
  const copy: JsonObject = isJsonObject(node) ? { ...node } : {};
  copy[key] = setIn(copy[key], rest, value);
  return copy;
}

export function setAt(root: JsonObject, path: Path, value: JsonValue): JsonObject {
  if (path.length === 0) return root;
  const result = setIn(root, path, value);
  return isJsonObject(result) ? result : root;
}

function unsetIn(node: JsonValue | undefined, path: Path): JsonValue | undefined {
  const [key, ...rest] = path;
  if (key === undefined) return node;
  if (typeof key === 'number') {
    if (!Array.isArray(node)) return node;
    const copy = [...node];
    if (rest.length === 0) copy.splice(key, 1);
    else if (key < copy.length) copy[key] = unsetIn(copy[key], rest) ?? null;
    return copy;
  }
  if (!isJsonObject(node)) return node;
  const copy: JsonObject = { ...node };
  if (rest.length === 0) {
    delete copy[key];
  } else if (key in copy) {
    const child = unsetIn(copy[key], rest);
    if (isJsonObject(child) && Object.keys(child).length === 0) delete copy[key];
    else if (child !== undefined) copy[key] = child;
  }
  return copy;
}

export function unsetAt(root: JsonObject, path: Path): JsonObject {
  const result = unsetIn(root, path);
  return isJsonObject(result) ? result : root;
}

function stringList(value: JsonValue | undefined): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function listWith(root: JsonObject, path: Path, item: string): JsonObject {
  const current = stringList(getAt(root, path));
  return setAt(root, path, current.includes(item) ? current : [...current, item]);
}

export function listWithout(root: JsonObject, path: Path, item: string): JsonObject {
  const next = stringList(getAt(root, path)).filter((entry) => entry !== item);
  return next.length === 0 ? unsetAt(root, path) : setAt(root, path, next);
}

export type ColumnRow = {
  name: string;
  dtype: string;
  inferredRole: string | null;
  role: string | null;
  isTarget: boolean;
  included: boolean;
  isId: boolean;
  nulls: number | null;
  unique: number | null;
};

const EXCLUDE: Path = ['features', 'exclude'];
const INCLUDE: Path = ['features', 'include'];
const ID_COLUMNS: Path = ['features', 'id_columns'];

function contains(value: JsonValue | undefined, name: string): boolean {
  return Array.isArray(value) && value.includes(name);
}

export function columnRows(profile: DatasetProfile, draft: JsonObject): ColumnRow[] {
  const include = getAt(draft, INCLUDE);
  return profile.columns.map((name) => {
    const role = getAt(draft, ['features', 'roles', name]);
    return {
      name,
      dtype: profile.dtypes[name] ?? '',
      inferredRole: profile.roles[name] ?? null,
      role: typeof role === 'string' ? role : null,
      isTarget: getAt(draft, ['target', 'column']) === name,
      included: (Array.isArray(include) ? include.includes(name) : true) && !contains(getAt(draft, EXCLUDE), name),
      isId: contains(getAt(draft, ID_COLUMNS), name),
      nulls: profile.null_counts[name] ?? null,
      unique: profile.n_unique[name] ?? null,
    };
  });
}

export function setTarget(draft: JsonObject, column: string): JsonObject {
  let next = setAt(draft, ['target', 'column'], column);
  next = listWithout(next, EXCLUDE, column);
  next = listWithout(next, INCLUDE, column);
  return listWithout(next, ID_COLUMNS, column);
}

export function setRole(draft: JsonObject, column: string, role: string | null): JsonObject {
  const path: Path = ['features', 'roles', column];
  return role === null ? unsetAt(draft, path) : setAt(draft, path, role);
}

export function setIncluded(draft: JsonObject, column: string, included: boolean): JsonObject {
  if (included) {
    const next = listWithout(draft, EXCLUDE, column);
    return Array.isArray(getAt(next, INCLUDE)) ? listWith(next, INCLUDE, column) : next;
  }
  return listWithout(listWith(draft, EXCLUDE, column), INCLUDE, column);
}

export function setIdColumn(draft: JsonObject, column: string, isId: boolean): JsonObject {
  return isId ? listWith(draft, ID_COLUMNS, column) : listWithout(draft, ID_COLUMNS, column);
}

export function parseContractText(text: string): { ok: true; contract: JsonObject } | { ok: false; error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, error: 'Not valid JSON.' };
  }
  if (!isJsonObject(parsed)) return { ok: false, error: 'The contract must be a JSON object.' };
  if (typeof getAt(parsed, ['target', 'column']) !== 'string') {
    return { ok: false, error: 'The contract needs target.column.' };
  }
  return { ok: true, contract: parsed };
}
