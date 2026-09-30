import { FILENAME_METADATA_TOKEN_PATTERN, type FilenameMetadataToken } from "./transcode-formatting-metadata";

const CONNECTOR_FILENAME_METADATA_TOKENS = new Set<FilenameMetadataToken>([
  "movieTitle", "releaseYear", "seriesName", "seasonNumber", "episodeNumber", "episodeTitle",
]);

type FilenameTemplatePart =
  | { type: "text"; value: string }
  | { type: "token"; token: FilenameMetadataToken };

function filenameTemplateParts(template: string): FilenameTemplatePart[] {
  const parts: FilenameTemplatePart[] = [];
  let cursor = 0;
  FILENAME_METADATA_TOKEN_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = FILENAME_METADATA_TOKEN_PATTERN.exec(template)) !== null) {
    if (match.index > cursor) parts.push({ type: "text", value: template.slice(cursor, match.index) });
    parts.push({ type: "token", token: match[1] as FilenameMetadataToken });
    cursor = match.index + match[0].length;
  }
  if (cursor < template.length) parts.push({ type: "text", value: template.slice(cursor) });
  return parts;
}

function escapeFilenameTemplateHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character] ?? character);
}

export function filenameTemplateEditorMarkup(template: string): string {
  return filenameTemplateParts(template).map((part) => {
    if (part.type === "text") {
      return `<span class="transcode-filename-inline-text">${escapeFilenameTemplateHtml(part.value)}</span>`;
    }
    const sourceClass = part.token === "sourceName"
      ? "is-source-name"
      : CONNECTOR_FILENAME_METADATA_TOKENS.has(part.token)
        ? "is-connector"
        : "is-medialyze";
    return `<span class="transcode-filename-inline-token ${sourceClass}" contenteditable="false" data-filename-token="${part.token}">{${part.token}}</span>`;
  }).join("");
}

function filenameTemplateNodeLength(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? "").length;
  if (node.nodeType !== Node.ELEMENT_NODE) return 0;
  const element = node as HTMLElement;
  const token = element.dataset.filenameToken;
  if (token) return `{${token}}`.length;
  if (element.tagName === "BR") return 1;
  return Array.from(node.childNodes).reduce((total, child) => total + filenameTemplateNodeLength(child), 0);
}

export function filenameTemplateFromEditor(root: HTMLElement): string {
  const serialize = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const element = node as HTMLElement;
    const token = element.dataset.filenameToken;
    if (token) return `{${token}}`;
    if (element.tagName === "BR") return "\n";
    const content = Array.from(node.childNodes).map(serialize).join("");
    return element !== root && (element.tagName === "DIV" || element.tagName === "P") ? `${content}\n` : content;
  };
  return Array.from(root.childNodes).map(serialize).join("").replace(/\u00a0/g, " ");
}

function filenameTemplateOffsetFromPoint(root: HTMLElement, container: Node, offset: number): number | null {
  if (container !== root && !root.contains(container)) return null;

  const measure = (node: Node): number | null => {
    if (node === container) {
      if (node.nodeType === Node.TEXT_NODE) return Math.min(offset, (node.textContent ?? "").length);
      const children = Array.from(node.childNodes);
      return children.slice(0, Math.min(offset, children.length)).reduce((total, child) => total + filenameTemplateNodeLength(child), 0);
    }
    if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).dataset.filenameToken) return null;
    let before = 0;
    for (const child of Array.from(node.childNodes)) {
      const result = measure(child);
      if (result !== null) return before + result;
      before += filenameTemplateNodeLength(child);
    }
    return null;
  };

  return measure(root);
}

export function filenameTemplateSelectionFromEditor(root: HTMLElement): { start: number; end: number } | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || !selection.anchorNode || !selection.focusNode) return null;
  const start = filenameTemplateOffsetFromPoint(root, selection.anchorNode, selection.anchorOffset);
  const end = filenameTemplateOffsetFromPoint(root, selection.focusNode, selection.focusOffset);
  if (start === null || end === null) return null;
  return start <= end ? { start, end } : { start: end, end: start };
}

export function restoreFilenameTemplateCaret(root: HTMLElement, targetOffset: number): void {
  const boundary = (() => {
    let consumed = 0;
    const visit = (node: Node): { container: Node; offset: number } | null => {
      if (node.nodeType === Node.TEXT_NODE) {
        const length = (node.textContent ?? "").length;
        if (targetOffset <= consumed + length) return { container: node, offset: Math.max(0, targetOffset - consumed) };
        consumed += length;
        return null;
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement;
        const token = element.dataset.filenameToken;
        if (token) {
          const length = `{${token}}`.length;
          const parent = node.parentNode;
          if (parent && targetOffset <= consumed + length) {
            const index = Array.prototype.indexOf.call(parent.childNodes, node) as number;
            return { container: parent, offset: targetOffset <= consumed ? index : index + 1 };
          }
          consumed += length;
          return null;
        }
      }
      for (const child of Array.from(node.childNodes)) {
        const result = visit(child);
        if (result) return result;
      }
      return null;
    };
    return visit(root) ?? { container: root, offset: root.childNodes.length };
  })();

  root.focus();
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  range.setStart(boundary.container, boundary.offset);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}
