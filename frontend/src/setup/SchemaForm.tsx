import { useState } from 'react';
import type { JsonObject, JsonValue, Vocabulary } from '../api/types.ts';
import { isJsonObject } from '../api/client.ts';
import { getAt, setAt, unsetAt, type Path } from '../contract/draft.ts';
import { fieldsOf, type FieldSpec } from '../contract/schema.ts';
import { pathKey } from '../ui/format.ts';

type FormProps = {
  schema: JsonObject;
  defName: string | null;
  path: Path;
  draft: JsonObject;
  onDraft: (draft: JsonObject) => void;
  columns: string[];
  vocabulary: Vocabulary;
};
type Parsed = { ok: true; value: JsonValue | undefined } | { ok: false; error: string };
type Option = { value: string; label: string };

const FEATURE_SKIPS = ['include', 'exclude', 'id_columns', 'roles'];

function splitList(text: string): string[] {
  return text.split(',').map((item) => item.trim()).filter((item) => item !== '');
}

function TextControl(props: {
  id: string; describedBy: string; initial: string; placeholder: string; multiline: boolean;
  inputType: 'text' | 'number'; step: string | null; parse: (text: string) => Parsed;
  onValue: (value: JsonValue | undefined) => void;
}) {
  const [text, setText] = useState(props.initial);
  const [error, setError] = useState<string | null>(null);
  function commit() {
    const parsed = props.parse(text.trim());
    if (parsed.ok) {
      setError(null);
      props.onValue(parsed.value);
    } else {
      setError(parsed.error);
    }
  }
  return (
    <>
      {props.multiline ? (
        <textarea
          id={props.id} rows={4} value={text} placeholder={props.placeholder} aria-describedby={props.describedBy}
          onChange={(event) => setText(event.target.value)} onBlur={commit}
        />
      ) : (
        <input
          id={props.id} type={props.inputType} {...(props.step === null ? {} : { step: props.step })}
          value={text} placeholder={props.placeholder} aria-describedby={props.describedBy}
          onChange={(event) => setText(event.target.value)} onBlur={commit}
        />
      )}
      {error !== null && <p role="alert">{error}</p>}
    </>
  );
}

function SelectControl(props: {
  id: string; describedBy: string; value: string; emptyLabel: string | null; options: Option[];
  onValue: (value: string) => void;
}) {
  return (
    <select
      id={props.id} value={props.value} aria-describedby={props.describedBy}
      onChange={(event) => props.onValue(event.target.value)}
    >
      {props.emptyLabel !== null && <option value="">{props.emptyLabel}</option>}
      {props.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  );
}

function CheckGroup(props: {
  id: string; legend: string; describedBy: string; options: Option[]; selected: string[];
  onValue: (selected: string[]) => void;
}) {
  return (
    <fieldset id={props.id} aria-describedby={props.describedBy}>
      <legend>{props.legend}</legend>
      {props.options.map((option) => (
        <label key={option.value} className="choice">
          <input
            type="checkbox" checked={props.selected.includes(option.value)}
            onChange={(event) => {
              const next = new Set(props.selected);
              if (event.target.checked) next.add(option.value);
              else next.delete(option.value);
              props.onValue(props.options.map((item) => item.value).filter((value) => next.has(value)));
            }}
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

function MapControl(props: {
  id: string; field: FieldSpec; value: JsonValue | undefined; columns: string[];
  onValue: (value: JsonObject | undefined) => void;
}) {
  const { field } = props;
  const entries: JsonObject = isJsonObject(props.value) ? props.value : {};
  const used = Object.keys(entries);
  const free = props.columns.filter((name) => !used.includes(name));
  const [column, setColumn] = useState('');
  const [text, setText] = useState('');
  const show = (item: JsonValue | undefined): string => (Array.isArray(item) ? item.join(', ') : String(item ?? ''));
  const addValue: JsonValue = field.kind === 'list-map' ? splitList(text) : text.trim();
  const canAdd = column !== '' && (field.kind === 'list-map' ? splitList(text).length > 0 : text.trim() !== '');
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">{field.title}</caption>
        <thead><tr><th>Column</th><th>Value</th><th>Remove</th></tr></thead>
        <tbody>
          {used.map((name) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{show(entries[name])}</td>
              <td>
                <button
                  type="button" aria-label={`Remove ${name}`}
                  onClick={() => {
                    const next: JsonObject = { ...entries };
                    delete next[name];
                    props.onValue(Object.keys(next).length === 0 ? undefined : next);
                  }}
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
          <tr>
            <td>
              <select aria-label={`${field.title} column`} value={column} onChange={(event) => setColumn(event.target.value)}>
                <option value="">Select a column</option>
                {free.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </td>
            <td>
              {field.kind === 'enum-map' ? (
                <select aria-label={`${field.title} value`} value={text} onChange={(event) => setText(event.target.value)}>
                  <option value="">Select a value</option>
                  {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              ) : (
                <input
                  type="text" aria-label={`${field.title} value`} value={text}
                  onChange={(event) => setText(event.target.value)}
                />
              )}
            </td>
            <td>
              <button
                type="button" disabled={!canAdd}
                onClick={() => {
                  props.onValue({ ...entries, [column]: addValue });
                  setColumn('');
                  setText('');
                }}
              >
                Add
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function stringsAt(value: JsonValue | undefined): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function Field(props: FormProps & { field: FieldSpec }) {
  const { draft, onDraft, columns, vocabulary, field } = props;
  const fieldPath: Path = [...props.path, field.key];
  const key = pathKey(fieldPath);
  const id = `f-${fieldPath.join('-')}`;
  const helpId = `${id}-help`;
  const value = getAt(draft, fieldPath);
  const set = (next: JsonValue) => onDraft(setAt(draft, fieldPath, next));
  const unset = () => onDraft(unsetAt(draft, fieldPath));
  const put = (next: JsonValue | undefined) => (next === undefined ? unset() : set(next));
  const hasDefault = field.defaultValue !== undefined && field.defaultValue !== null;
  const help = hasDefault ? `Default: ${JSON.stringify(field.defaultValue)}` : 'Not set by default';
  const columnOptions: Option[] = columns.map((name) => ({ value: name, label: name }));
  const textValue = typeof value === 'string' ? value : '';
  const placeholder = hasDefault ? String(field.defaultValue) : '';

  if (field.kind === 'object') {
    if (field.ref === null) return null;
    return (
      <fieldset>
        <legend>{field.title}</legend>
        <SchemaForm {...props} defName={field.ref} path={fieldPath} />
      </fieldset>
    );
  }
  if (field.kind === 'object-list') {
    if (field.ref === null) return null;
    const items = Array.isArray(value) ? value : [];
    return (
      <fieldset>
        <legend>{field.title}</legend>
        {items.map((_, index) => (
          <fieldset key={index}>
            <legend>{`Goal ${index + 1}`}</legend>
            <SchemaForm {...props} defName={field.ref} path={[...fieldPath, index]} />
            <button
              type="button"
              onClick={() => onDraft(unsetAt(draft, items.length === 1 ? fieldPath : [...fieldPath, index]))}
            >
              Remove goal
            </button>
          </fieldset>
        ))}
        <button type="button" onClick={() => set([...items, {}])}>Add goal</button>
      </fieldset>
    );
  }

  let control;
  if (key === 'target.column' || key === 'split.time_column' || key === 'split.group_column') {
    control = (
      <SelectControl
        id={id} describedBy={helpId} value={textValue} options={columnOptions}
        emptyLabel={key === 'target.column' ? null : 'Not set'}
        onValue={(next) => (next === '' ? unset() : set(next))}
      />
    );
  } else if (key === 'modelling.candidates') {
    const task = getAt(draft, ['objective', 'task']);
    const options = vocabulary.estimators
      .filter((estimator) => typeof task !== 'string' || estimator.task === task)
      .map((estimator) => ({ value: estimator.name, label: `${estimator.name} (${estimator.task})` }));
    control = (
      <CheckGroup
        id={id} legend={field.title} describedBy={helpId} options={options} selected={stringsAt(value)}
        onValue={(next) => (next.length === 0 ? unset() : set(next))}
      />
    );
  } else if (key === 'modelling.inferential_model') {
    control = (
      <SelectControl
        id={id} describedBy={helpId} value={textValue} emptyLabel="Chosen from the target type"
        options={vocabulary.inferentialModels.map((name) => ({ value: name, label: name }))}
        onValue={(next) => (next === '' ? unset() : set(next))}
      />
    );
  } else if (key.endsWith('.predictors')) {
    control = (
      <CheckGroup
        id={id} legend={field.title} describedBy={helpId} options={columnOptions} selected={stringsAt(value)}
        onValue={(next) => (next.length === 0 ? unset() : set(next))}
      />
    );
  } else if (field.kind === 'enum') {
    control = (
      <SelectControl
        id={id} describedBy={helpId} value={textValue}
        emptyLabel={hasDefault ? `Default (${String(field.defaultValue)})` : 'Not set'}
        options={field.options.map((option) => ({ value: option, label: option }))}
        onValue={(next) => (next === '' ? unset() : set(next))}
      />
    );
  } else if (field.kind === 'boolean') {
    control = (
      <SelectControl
        id={id} describedBy={helpId} value={typeof value === 'boolean' ? String(value) : ''}
        emptyLabel={hasDefault ? `Default (${field.defaultValue === true ? 'yes' : 'no'})` : 'Not set'}
        options={[{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]}
        onValue={(next) => (next === '' ? unset() : set(next === 'true'))}
      />
    );
  } else if (field.kind === 'integer' || field.kind === 'number') {
    const whole = field.kind === 'integer';
    control = (
      <TextControl
        key={JSON.stringify(value) ?? ''} id={id} describedBy={helpId} initial={typeof value === 'number' ? String(value) : ''} placeholder={placeholder}
        multiline={false} inputType="number" step={whole ? '1' : 'any'} onValue={put}
        parse={(text) => {
          if (text === '') return { ok: true, value: undefined };
          const parsed = Number(text);
          if (Number.isFinite(parsed) && (!whole || Number.isInteger(parsed))) return { ok: true, value: parsed };
          return { ok: false, error: whole ? 'Enter a whole number.' : 'Enter a number.' };
        }}
      />
    );
  } else if (field.kind === 'string') {
    control = (
      <TextControl
        key={JSON.stringify(value) ?? ''} id={id} describedBy={helpId} initial={textValue} placeholder={placeholder} multiline={field.key === 'notes'}
        inputType="text" step={null} onValue={put}
        parse={(text) => ({ ok: true, value: text === '' ? undefined : text })}
      />
    );
  } else if (field.kind === 'string-list') {
    control = (
      <TextControl
        key={JSON.stringify(value) ?? ''} id={id} describedBy={helpId} initial={stringsAt(value).join(', ')} placeholder={placeholder}
        multiline={false} inputType="text" step={null} onValue={put}
        parse={(text) => {
          const items = splitList(text);
          return { ok: true, value: items.length === 0 ? undefined : items };
        }}
      />
    );
  } else if (field.kind === 'enum-list') {
    control = (
      <CheckGroup
        id={id} legend={field.title} describedBy={helpId}
        options={field.options.map((option) => ({ value: option, label: option }))} selected={stringsAt(value)}
        onValue={(next) => (next.length === 0 ? unset() : set(next))}
      />
    );
  } else if (field.kind === 'enum-map' || field.kind === 'list-map' || field.kind === 'string-map') {
    control = <MapControl id={id} field={field} value={value} columns={columns} onValue={put} />;
  } else {
    control = (
      <TextControl
        key={JSON.stringify(value) ?? ''} id={id} describedBy={helpId} initial={value === undefined ? '' : JSON.stringify(value)} placeholder=""
        multiline={false} inputType="text" step={null} onValue={put}
        parse={(text) => {
          if (text === '') return { ok: true, value: undefined };
          try {
            const parsed: unknown = JSON.parse(text);
            if (typeof parsed === 'string' || typeof parsed === 'number' || typeof parsed === 'boolean') {
              return { ok: true, value: parsed };
            }
          } catch {
            // fall through to the shared error below
          }
          return { ok: false, error: 'Enter a value such as 1, true or "yes".' };
        }}
      />
    );
  }

  const groupLabelled = field.kind === 'enum-list' || key === 'modelling.candidates' || key.endsWith('.predictors');
  return (
    <div className="field">
      {!groupLabelled && <label htmlFor={id}>{field.title}</label>}
      {control}
      <p className="help" id={helpId}>{help}</p>
    </div>
  );
}

export default function SchemaForm(props: FormProps) {
  const { schema, defName, path, draft, onDraft, columns, vocabulary } = props;
  const all = fieldsOf(schema, defName).filter(
    (field) => !(defName === 'FeatureRules' && FEATURE_SKIPS.includes(field.key)),
  );
  const fields = defName === null
    ? [...all.filter((field) => field.key === 'target'), ...all.filter((field) => field.key !== 'target')]
    : all;
  return (
    <>
      {fields.map((field) => (
        <Field
          key={field.key}
          schema={schema} defName={defName} path={path} draft={draft} onDraft={onDraft}
          columns={columns} vocabulary={vocabulary} field={field}
        />
      ))}
    </>
  );
}
