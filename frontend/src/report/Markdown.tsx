import type { ReactNode } from 'react';
import { figureRow, parseMarkdown, type Inline } from './markdown.ts';

type ImageUrl = (src: string) => string | null;

function inlines(items: Inline[], imageUrl: ImageUrl): ReactNode[] {
  return items.map((item, index) => {
    if (item.type === 'text') return item.text;
    if (item.type === 'code') return <code key={index}>{item.text}</code>;
    if (item.type === 'strong') return <strong key={index}>{inlines(item.children, imageUrl)}</strong>;
    if (item.type === 'em') return <em key={index}>{inlines(item.children, imageUrl)}</em>;
    if (item.type === 'link') {
      return /^https?:\/\//i.test(item.href)
        ? <a key={index} href={item.href} rel="noreferrer noopener" target="_blank">{inlines(item.children, imageUrl)}</a>
        : <span key={index}>{inlines(item.children, imageUrl)}</span>;
    }
    const url = imageUrl(item.src);
    return url === null ? `[figure: ${item.alt}]` : <img key={index} className="plot" alt={item.alt} src={url} />;
  });
}

export default function Markdown({ source, imageUrl }: { source: string; imageUrl: ImageUrl }) {
  return (
    <>
      {parseMarkdown(source).map((block, index) => {
        if (block.type === 'heading') {
          const Tag = `h${Math.min(block.level + 1, 6)}` as 'h2';
          return <Tag key={index}>{inlines(block.inlines, imageUrl)}</Tag>;
        }
        if (block.type === 'paragraph') {
          const figures = figureRow(block.inlines);
          if (figures !== null) {
            return (
              <div className="plot-grid" key={index}>
                {figures.map((fig, at) => {
                  const url = imageUrl(fig.src);
                  return url === null
                    ? <p key={at}>{`[figure: ${fig.alt}]`}</p>
                    : <a key={at} href={url} target="_blank" rel="noreferrer noopener" title="Open full size"><img className="plot" alt={fig.alt} src={url} /></a>;
                })}
              </div>
            );
          }
          return <p key={index}>{inlines(block.inlines, imageUrl)}</p>;
        }
        if (block.type === 'code') return <pre key={index}><code>{block.text}</code></pre>;
        if (block.type === 'quote') return <blockquote key={index}>{inlines(block.inlines, imageUrl)}</blockquote>;
        if (block.type === 'rule') return <hr key={index} />;
        if (block.type === 'list') {
          const items = block.items.map((item, at) => <li key={at}>{inlines(item, imageUrl)}</li>);
          return block.ordered ? <ol key={index}>{items}</ol> : <ul key={index}>{items}</ul>;
        }
        return (
          <div className="table-wrap" key={index}>
            <table>
              <thead>
                <tr>{block.header.map((cell, at) => <th key={at} scope="col">{inlines(cell, imageUrl)}</th>)}</tr>
              </thead>
              <tbody>
                {block.rows.map((row, at) => (
                  <tr key={at}>{row.map((cell, cellAt) => <td key={cellAt}>{inlines(cell, imageUrl)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
