// SPDX-License-Identifier: AGPL-3.0-only
// Card text is Markdown with KaTeX math. It comes from users, imports, shared
// decks and AI output, so it is untrusted: the Markdown is rendered to HTML and
// sanitized with DOMPurify before it reaches the DOM.

import DOMPurify from "dompurify";
import katex from "katex";
import { marked } from "marked";

marked.use({ gfm: true, breaks: true, async: false });

let hooksInstalled = false;

/** Images come only from this origin (the app and its API) or inline data, so card text cannot track who studies it. */
function allowedImageSource(src: string): boolean {
  if (/^data:image\/(png|jpeg|gif|webp);/i.test(src)) {
    return true;
  }
  try {
    const url = new URL(src, window.location.href);
    return url.origin === window.location.origin;
  } catch {
    return false; // not a URL at all
  }
}

// The only classes card text may carry: the cloze marks this app adds itself.
const ALLOWED_CLASS = /^cloze( cloze-hidden)?$/;

function installHooks(): void {
  if (hooksInstalled) {
    return;
  }
  hooksInstalled = true;
  DOMPurify.addHook("uponSanitizeAttribute", (_node, data) => {
    if (data.attrName === "class" && !ALLOWED_CLASS.test(data.attrValue)) {
      data.keepAttr = false;
    }
  });
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
    if (node.tagName === "IMG" && !allowedImageSource(node.getAttribute("src") ?? "")) {
      node.removeAttribute("src");
    }
  });
}

// Code is left alone; math is only looked for in the text around it.
const CODE = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/;
const MATH =
  /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])/g;

interface MathPart {
  tex: string;
  display: boolean;
}

function extractMath(source: string): { text: string; parts: MathPart[] } {
  const parts: MathPart[] = [];
  const text = source
    .split(CODE)
    .map((segment, index) =>
      index % 2 === 1
        ? segment
        : segment.replace(
            MATH,
            (
              _match,
              display: string | undefined,
              bracket: string | undefined,
              paren: string | undefined,
              inline: string | undefined,
            ) => {
              const isDisplay = display !== undefined || bracket !== undefined;
              // Exactly one group matched, so one of these is set.
              const tex = (display ?? bracket ?? paren ?? inline) as string;
              parts.push({ tex, display: isDisplay });
              return `MATHPLACEHOLDER${parts.length - 1}END`;
            },
          ),
    )
    .join("");
  return { text, parts };
}

function renderMath(part: MathPart): string {
  // trust: false keeps \href, \url and \htmlClass out, so the output holds no
  // links, scripts or user-chosen attributes; user text in it is escaped.
  return katex.renderToString(part.tex, {
    displayMode: part.display,
    throwOnError: false,
    output: "htmlAndMathml",
    trust: false,
    strict: "ignore",
  });
}

const PLACEHOLDER = /MATHPLACEHOLDER(\d+)END/g;

/** Swap placeholders for KaTeX output in text nodes only, never inside an attribute. */
function insertMath(root: Node, parts: MathPart[]): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if ((node.nodeValue ?? "").includes("MATHPLACEHOLDER")) {
      texts.push(node as Text);
    }
  }
  for (const node of texts) {
    const holder = document.createElement("span");
    holder.innerHTML = node.data
      .split(PLACEHOLDER)
      .map((piece, index) => {
        if (index % 2 === 0) {
          return piece.replace(/[&<>]/g, (c) => `&#${String(c.charCodeAt(0))};`);
        }
        const part = parts[Number(piece)];
        return part === undefined ? "" : renderMath(part);
      })
      .join("");
    node.replaceWith(...holder.childNodes);
  }
}

/**
 * Markdown and math to sanitized HTML. The Markdown is sanitized on its own,
 * with no style or id and no class but the cloze marks, so card text cannot
 * restyle or imitate the app. KaTeX output is then put into text positions
 * and keeps the inline styles it needs.
 */
export function renderMarkdown(source: string, options: { math?: boolean } = {}): string {
  installHooks();
  // Prose such as a legal page has no math, and "$5 or $10" must stay as written.
  const { text, parts } = options.math === false ? { text: source, parts: [] } : extractMath(source);
  const fragment = DOMPurify.sanitize(marked.parse(text, { async: false }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select"],
    FORBID_ATTR: ["style", "id", "srcset"],
    RETURN_DOM_FRAGMENT: true,
  });
  if (options.math !== false) {
    insertMath(fragment, parts);
  }
  const container = document.createElement("div");
  container.append(fragment);
  return container.innerHTML;
}
