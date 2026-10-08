export type Inline =
  | { type: 'text'; text: string }
  | { type: 'code'; text: string }
  | { type: 'strong'; children: Inline[] }
  | { type: 'em'; children: Inline[] }
  | { type: 'link'; href: string; children: Inline[] }
  | { type: 'image'; alt: string; src: string };
export type Block =
  | { type: 'heading'; level: number; inlines: Inline[] }
  | { type: 'paragraph'; inlines: Inline[] }
  | { type: 'code'; text: string }
  | { type: 'list'; ordered: boolean; items: Inline[][] }
  | { type: 'quote'; inlines: Inline[] }
  | { type: 'table'; header: Inline[][]; rows: Inline[][][] }
  | { type: 'rule' };

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let literal = '';
  const flush = () => {
    if (literal !== '') out.push({ type: 'text', text: literal });
    literal = '';
  };
  let i = 0;
  while (i < text.length) {
    const ch = text.charAt(i);
    if (ch === '\\' && i + 1 < text.length) {
      literal += text.charAt(i + 1);
      i += 2;
      continue;
    }
    if (ch === '`') {
      const end = text.indexOf('`', i + 1);
      if (end !== -1) {
        flush();
        out.push({ type: 'code', text: text.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    if (ch === '!' && text.charAt(i + 1) === '[') {
      const match = /^!\[([^\]]*)\]\(([^)\s]+)\)/.exec(text.slice(i));
      if (match !== null) {
        flush();
        out.push({ type: 'image', alt: match[1] ?? '', src: match[2] ?? '' });
        i += match[0].length;
        continue;
      }
    }
    if (ch === '[') {
      const match = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(text.slice(i));
      if (match !== null) {
        flush();
        out.push({ type: 'link', href: match[2] ?? '', children: parseInline(match[1] ?? '') });
        i += match[0].length;
        continue;
      }
    }
    if (ch === '*' && text.charAt(i + 1) === '*') {
      const end = text.indexOf('**', i + 2);
      if (end > i + 2) {
        flush();
        out.push({ type: 'strong', children: parseInline(text.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
      literal += '**';
      i += 2;
      continue;
    }
    if (ch === '*') {
      let end = -1;
      for (let j = i + 1; j < text.length; j += 1) {
        if (text.charAt(j) === '*' && text.charAt(j + 1) !== '*' && text.charAt(j - 1) !== '*') {
          end = j;
          break;
        }
      }
      if (end > i + 1) {
        flush();
        out.push({ type: 'em', children: parseInline(text.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }
    literal += ch;
    i += 1;
  }
  flush();
  return out;
}

const SEPARATOR = /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function splitRow(line: string): Inline[][] {
  let body = line.trim();
  if (body.startsWith('|')) body = body.slice(1);
  if (body.endsWith('|') && !body.endsWith('\\|')) body = body.slice(0, -1);
  return body.split(/(?<!\\)\|/).map((cell) => parseInline(cell.trim()));
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.split('\n').map((line) => line.replace(/\r$/, ''));
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const endParagraph = () => {
    if (paragraph.length > 0) blocks.push({ type: 'paragraph', inlines: parseInline(paragraph.join(' ')) });
    paragraph = [];
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? '';
    if (line.trim() === '') {
      endParagraph();
      i += 1;
      continue;
    }
    if (line.startsWith('```')) {
      endParagraph();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? '').startsWith('```')) {
        code.push(lines[i] ?? '');
        i += 1;
      }
      i += 1;
      blocks.push({ type: 'code', text: code.join('\n') });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading !== null) {
      endParagraph();
      blocks.push({ type: 'heading', level: (heading[1] ?? '').length, inlines: parseInline((heading[2] ?? '').replace(/\s*#+\s*$/, '')) });
      i += 1;
      continue;
    }
    if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      endParagraph();
      blocks.push({ type: 'rule' });
      i += 1;
      continue;
    }
    if (line.startsWith('|') && SEPARATOR.test(lines[i + 1] ?? '')) {
      endParagraph();
      const header = splitRow(line);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && (lines[i] ?? '').startsWith('|')) {
        rows.push(splitRow(lines[i] ?? ''));
        i += 1;
      }
      blocks.push({ type: 'table', header, rows });
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? numbered;
    if (item !== null) {
      endParagraph();
      const ordered = bullet === null;
      const last = blocks[blocks.length - 1];
      const inlines = parseInline(item[1] ?? '');
      if (last !== undefined && last.type === 'list' && last.ordered === ordered && paragraph.length === 0) last.items.push(inlines);
      else blocks.push({ type: 'list', ordered, items: [inlines] });
      i += 1;
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote !== null) {
      endParagraph();
      const last = blocks[blocks.length - 1];
      if (last !== undefined && last.type === 'quote') last.inlines.push({ type: 'text', text: ' ' }, ...parseInline(quote[1] ?? ''));
      else blocks.push({ type: 'quote', inlines: parseInline(quote[1] ?? '') });
      i += 1;
      continue;
    }
    paragraph.push(line);
    i += 1;
  }
  endParagraph();
  return blocks;
}
