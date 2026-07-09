export function canAttemptClipboardCopy(): boolean {
  if (typeof navigator !== "undefined" && Boolean(navigator.clipboard?.writeText)) {
    return true;
  }
  return typeof document !== "undefined" && typeof document.execCommand === "function";
}

function copyWithTextArea(text: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") {
    return false;
  }

  const textArea = document.createElement("textarea");
  textArea.value = text;
  textArea.setAttribute("readonly", "");
  textArea.style.position = "fixed";
  textArea.style.top = "0";
  textArea.style.left = "0";
  textArea.style.width = "1px";
  textArea.style.height = "1px";
  textArea.style.opacity = "0";

  const selection = document.getSelection();
  const selectedRanges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange())
    : [];
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.body.removeChild(textArea);
    if (selection) {
      selection.removeAllRanges();
      selectedRanges.forEach((range) => selection.addRange(range));
    }
    activeElement?.focus();
  }

  return copied;
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
  const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // Fall back for browsers that expose the async API but reject it on insecure origins.
    }
  }

  return copyWithTextArea(text);
}
