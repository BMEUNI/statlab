import assert from 'node:assert/strict';
import { test } from 'node:test';
import { figureRow, parseInline, parseMarkdown } from '../src/report/markdown.ts';

const text = (value: string) => ({ type: 'text', text: value });

test('parseInline handles emphasis, code, escapes, links and images', () => {
  assert.deepEqual(parseInline('a **b** *c* `d`'), [
    text('a '), { type: 'strong', children: [text('b')] }, text(' '),
    { type: 'em', children: [text('c')] }, text(' '), { type: 'code', text: 'd' },
  ]);
  assert.deepEqual(parseInline('a \\* b'), [text('a * b')]);
  assert.deepEqual(parseInline('**x'), [text('**x')]);
  assert.deepEqual(parseInline('balanced_accuracy'), [text('balanced_accuracy')]);
  assert.deepEqual(parseInline('[l](https://x.y)'), [{ type: 'link', href: 'https://x.y', children: [text('l')] }]);
  assert.deepEqual(parseInline('![alt](plots/a.png)'), [{ type: 'image', alt: 'alt', src: 'plots/a.png' }]);
  assert.deepEqual(parseInline('<script>x</script>'), [text('<script>x</script>')]);
});

test('parseMarkdown block structure', () => {
  assert.deepEqual(parseMarkdown('## Title ##'), [{ type: 'heading', level: 2, inlines: [text('Title')] }]);
  assert.deepEqual(parseMarkdown('a\nb\n\nc'), [
    { type: 'paragraph', inlines: [text('a b')] }, { type: 'paragraph', inlines: [text('c')] },
  ]);
  assert.deepEqual(parseMarkdown('```\n**x**\n```'), [{ type: 'code', text: '**x**' }]);
  assert.deepEqual(parseMarkdown('- a\n- b\n1. c'), [
    { type: 'list', ordered: false, items: [[text('a')], [text('b')]] },
    { type: 'list', ordered: true, items: [[text('c')]] },
  ]);
  assert.deepEqual(parseMarkdown('> a\n> b'), [{ type: 'quote', inlines: [text('a'), text(' '), text('b')] }]);
  assert.deepEqual(parseMarkdown('---'), [{ type: 'rule' }]);
  assert.deepEqual(parseMarkdown('| h |\n| --- |\n| a \\| b |'), [
    { type: 'table', header: [[text('h')]], rows: [[[text('a | b')]]] },
  ]);
  assert.deepEqual(parseMarkdown('| not a table'), [{ type: 'paragraph', inlines: [text('| not a table')] }]);
});

test('parseMarkdown on a report head', () => {
  const source = '# Modelling report: churned\n\n## Result\n\nChampion **c_01** won.\n\n| Metric | Score |\n| --- | --- |\n| auc | 0.8 |\n| acc | 0.7 |\n';
  assert.deepEqual(parseMarkdown(source).map((block) => block.type), ['heading', 'heading', 'paragraph', 'table']);
  const table = parseMarkdown(source)[3];
  assert.equal(table?.type === 'table' ? table.rows.length : -1, 2);
});

test('figureRow keeps only image-only paragraphs', () => {
  assert.deepEqual(figureRow(parseInline('![a](plots/a.png) ![b](plots/b.png)')), [
    { alt: 'a', src: 'plots/a.png' },
    { alt: 'b', src: 'plots/b.png' },
  ]);
  assert.equal(figureRow(parseInline('see ![a](plots/a.png)')), null);
  assert.equal(figureRow(parseInline('plain')), null);
});
