// SPDX-License-Identifier: AGPL-3.0-only
// Card text is Markdown with KaTeX math. It comes from users, imports and AI
// output, so it is untrusted: everything is rendered to HTML and then passed
// through DOMPurify before it reaches the DOM.

import DOMPurify from "dompurify";
import katex from "katex";
import { marked } from "marked";

marked.use({ gfm: true, breaks: true, async: false });

let hooksInstalled = false;

function installHooks(): void {
  if (hooksInstalled) {
    return;
  }
  hooksInstalled = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

// Code is left alone; math is only looked for in the text around it.
const CODE = /(```[\s\S]*?```|`[^`\n]*`)/;
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
  return katex.renderToString(part.tex, {
    displayMode: part.display,
    throwOnError: false,
    output: "htmlAndMathml",
    trust: false,
    strict: "ignore",
  });
}

/** Markdown and math to sanitized HTML. */
export function renderMarkdown(source: string): string {
  installHooks();
  const { text, parts } = extractMath(source);
  const html = marked.parse(text, { async: false });
  const withMath = html.replace(/MATHPLACEHOLDER(\d+)END/g, (_match, index: string) => {
    const part = parts[Number(index)];
    return part === undefined ? "" : renderMath(part);
  });
  return DOMPurify.sanitize(withMath, {
    USE_PROFILES: { html: true, mathMl: true, svg: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select"],
    FORBID_ATTR: ["srcset"],
  });
}
