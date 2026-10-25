import type { ReactNode } from "react";

const inlinePattern =
  /(\*\*.+?\*\*|__.+?__|~~.+?~~|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\)|https?:\/\/[^\s<]+)/;
const unorderedItem = /^\s*[-*+]\s+(.+)$/;
const orderedItem = /^\s*(\d+)[.)]\s+(.+)$/;
const heading = /^(#{1,6})\s+(.+)$/;

function safeHref(value: string) {
  try {
    const url = new URL(value);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = new RegExp(inlinePattern.source, "g");
  let offset = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > offset) nodes.push(text.slice(offset, match.index));
    const token = match[0];
    const key = `${match.index}-${token}`;
    const link = token.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/);

    if (link) {
      const href = safeHref(link[2]);
      nodes.push(href
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{renderInline(link[1])}</a>
        : token);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(<strong key={key}>{renderInline(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("~~")) {
      nodes.push(<del key={key}>{renderInline(token.slice(2, -2))}</del>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("*") || token.startsWith("_")) {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1))}</em>);
    } else {
      const trimmed = token.replace(/[.,!?;:]+$/, "");
      const href = safeHref(trimmed);
      if (href) {
        nodes.push(<a key={key} href={href} target="_blank" rel="noopener noreferrer">{trimmed}</a>);
        if (trimmed.length < token.length) nodes.push(token.slice(trimmed.length));
      } else {
        nodes.push(token);
      }
    }
    offset = pattern.lastIndex;
  }

  if (offset < text.length) nodes.push(text.slice(offset));
  return nodes;
}

function isBlockStart(line: string) {
  return line.startsWith("```") || heading.test(line) || unorderedItem.test(line) || orderedItem.test(line) ||
    /^\s*>\s?/.test(line) || /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line);
}

function splitTableRow(line: string) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

export function Markdown({ children }: { children: string }) {
  const lines = children.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }

    if (line.startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(<pre key={`code-${index}`}><code>{code.join("\n")}</code></pre>);
      continue;
    }

    const headingMatch = line.match(heading);
    if (headingMatch) {
      const Tag = `h${headingMatch[1].length}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={index}>{renderInline(headingMatch[2])}</Tag>);
      index += 1;
      continue;
    }

    if (index + 1 < lines.length && line.includes("|") &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1]) &&
      lines[index + 1].includes("-")) {
      const headers = splitTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim().includes("|")) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <table key={`table-${index}`}>
          <thead><tr>{headers.map((cell, cellIndex) => <th key={cellIndex}>{renderInline(cell)}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => (
            <tr key={rowIndex}>{headers.map((_, cellIndex) => <td key={cellIndex}>{renderInline(row[cellIndex] || "")}</td>)}</tr>
          ))}</tbody>
        </table>,
      );
      continue;
    }

    if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      blocks.push(<hr key={index} />);
      index += 1;
      continue;
    }

    if (line.match(unorderedItem) || line.match(orderedItem)) {
      const isOrdered = Boolean(line.match(orderedItem));
      const items: string[] = [];
      const start = isOrdered ? Number(line.match(orderedItem)?.[1]) : undefined;
      while (index < lines.length) {
        const item = lines[index].trim().match(isOrdered ? orderedItem : unorderedItem);
        if (!item) break;
        items.push(isOrdered ? item[2] : item[1]);
        index += 1;
      }
      blocks.push(isOrdered
        ? <ol key={`list-${index}`} start={start}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item)}</li>)}</ol>
        : <ul key={`list-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInline(item)}</li>)}</ul>);
      continue;
    }

    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(<blockquote key={`quote-${index}`}>{renderInline(quote.join(" "))}</blockquote>);
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index].trim())) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(
      <p key={`paragraph-${index}`}>
        {paragraph.map((part, partIndex) => (
          <span key={partIndex}>{partIndex > 0 && <br />}{renderInline(part)}</span>
        ))}
      </p>,
    );
  }

  return <div className="markdown-content">{blocks}</div>;
}
