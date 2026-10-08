import { isJsonObject } from '../api/client.ts';
import type { JsonObject, JsonValue } from '../api/types.ts';

export type FieldKind =
  | 'enum' | 'boolean' | 'integer' | 'number' | 'string' | 'string-list' | 'enum-list'
  | 'enum-map' | 'list-map' | 'string-map' | 'object' | 'object-list' | 'json';
export type FieldSpec = {
  key: string;
  title: string;
  description: string | null;
  kind: FieldKind;
  nullable: boolean;
  options: string[];
  defaultValue: JsonValue | undefined;
  ref: string | null;
};
type Classified = { kind: FieldKind; options: string[]; ref: string | null };

const JSON_KIND: Classified = { kind: 'json', options: [], ref: null };

export function resolveDef(schema: JsonObject, name: string): JsonObject | null {
  const defs = schema.$defs;
  const def = isJsonObject(defs) ? defs[name] : undefined;
  return isJsonObject(def) ? def : null;
}

export function refName(value: JsonObject): string | null {
  const ref = value.$ref;
  return typeof ref === 'string' && ref.startsWith('#/$defs/') ? ref.slice('#/$defs/'.length) : null;
}

function enumOptions(value: JsonValue | undefined): string[] | null {
  if (!Array.isArray(value)) return null;
  const options = value.filter((item): item is string => typeof item === 'string');
  return options.length === value.length ? options : null;
}

function enumUnion(members: JsonValue[]): string[] | null {
  const options: string[] = [];
  for (const member of members) {
    const found = isJsonObject(member) ? enumOptions(member.enum) : null;
    if (found === null) return null;
    options.push(...found);
  }
  return options;
}

function classifyItems(items: JsonValue | undefined): Classified {
  if (!isJsonObject(items)) return JSON_KIND;
  const ref = refName(items);
  if (ref !== null) return { kind: 'object-list', options: [], ref };
  const options = enumOptions(items.enum);
  if (options !== null) return { kind: 'enum-list', options, ref: null };
  if (Array.isArray(items.anyOf)) {
    const union = enumUnion(items.anyOf);
    if (union !== null) return { kind: 'enum-list', options: union, ref: null };
  }
  return items.type === 'string' ? { kind: 'string-list', options: [], ref: null } : JSON_KIND;
}

function classifyMap(extra: JsonValue | undefined): Classified {
  if (!isJsonObject(extra)) return JSON_KIND;
  const options = enumOptions(extra.enum);
  if (options !== null) return { kind: 'enum-map', options, ref: null };
  if (extra.type === 'array') return { kind: 'list-map', options: [], ref: null };
  if (extra.type === 'string') return { kind: 'string-map', options: [], ref: null };
  return JSON_KIND;
}

export function classify(member: JsonObject): Classified {
  const ref = refName(member);
  if (ref !== null) return { kind: 'object', options: [], ref };
  const options = enumOptions(member.enum);
  if (options !== null) return { kind: 'enum', options, ref: null };
  const type = member.type;
  if (type === 'boolean' || type === 'integer' || type === 'number' || type === 'string') {
    return { kind: type, options: [], ref: null };
  }
  if (type === 'array') return classifyItems(member.items);
  if (type === 'object' && member.additionalProperties !== undefined) return classifyMap(member.additionalProperties);
  return JSON_KIND;
}

function fieldOf(key: string, property: JsonObject): FieldSpec {
  const title = typeof property.title === 'string' ? property.title : key;
  const description = typeof property.description === 'string' && property.description.trim() !== '' ? property.description : null;
  const base = { key, title, description, defaultValue: property.default };
  if (!Array.isArray(property.anyOf)) return { ...base, nullable: false, ...classify(property) };
  const members = property.anyOf.filter(isJsonObject);
  const isNull = (member: JsonObject): boolean => member.type === 'null';
  const nonNull = members.filter((member) => !isNull(member));
  const nullable = members.some(isNull);
  const union = enumUnion(nonNull);
  if (union !== null) return { ...base, nullable, kind: 'enum', options: union, ref: null };
  const [only] = nonNull;
  if (nonNull.length === 1 && only !== undefined) return { ...base, nullable, ...classify(only) };
  return { ...base, nullable, ...JSON_KIND };
}

export function fieldsOf(schema: JsonObject, defName: string | null): FieldSpec[] {
  const source = defName === null ? schema : resolveDef(schema, defName);
  const properties = source?.properties;
  if (!isJsonObject(properties)) return [];
  const fields: FieldSpec[] = [];
  for (const [key, property] of Object.entries(properties)) {
    if (!isJsonObject(property) || 'const' in property) continue;
    fields.push(fieldOf(key, property));
  }
  return fields;
}
