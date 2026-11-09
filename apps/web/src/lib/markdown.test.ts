// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

function dom(html: string): HTMLElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

describe("renderMarkdown", () => {
  it("renders Markdown", () => {
    const html = renderMarkdown("**bold** and _it_\n\n- one");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<em>it</em>");
    expect(html).toContain("<li>one</li>");
  });

  it("renders inline and display math in every delimiter style", () => {
    for (const source of ["$x^2$", "$$x^2$$", "\\(x^2\\)", "\\[x^2\\]"]) {
      const root = dom(renderMarkdown(source));
      expect(root.querySelector(".katex")).not.toBeNull();
    }
    expect(dom(renderMarkdown("$$x$$")).querySelector(".katex-display")).not.toBeNull();
    expect(dom(renderMarkdown("$x$")).querySelector(".katex-display")).toBeNull();
  });

  it("keeps the MathML that screen readers use", () => {
    expect(dom(renderMarkdown("$x^2$")).querySelector("math")).not.toBeNull();
  });

  it("leaves prices and code alone", () => {
    expect(renderMarkdown("costs $5 and $6")).not.toContain("katex");
    const code = renderMarkdown("`$x$` and\n\n```\n$$y$$\n```");
    expect(code).not.toContain("katex");
    expect(code).toContain("$x$");
  });

  it("shows a KaTeX error inline instead of throwing", () => {
    expect(() => renderMarkdown("$\\frac{$")).not.toThrow();
  });

  it("drops a placeholder that does not refer to math", () => {
    expect(renderMarkdown("MATHPLACEHOLDER7END")).not.toContain("MATHPLACEHOLDER");
  });

  it("removes scripts, event handlers and javascript links", () => {
    const html = renderMarkdown(
      '<script>alert(1)</script><img src="x" onerror="alert(1)"><a href="javascript:alert(1)">x</a>[y](javascript:alert(2))<iframe src="https://evil"></iframe><form><input></form><style>*{}</style>',
    );
    const root = dom(html);
    expect(root.querySelector("script, iframe, form, input, style")).toBeNull();
    expect(root.querySelector("img")?.getAttribute("onerror")).toBeNull();
    for (const link of root.querySelectorAll("a")) {
      expect(link.getAttribute("href") ?? "").not.toMatch(/javascript/i);
    }
  });

  it("does not let math source smuggle markup", () => {
    const root = dom(renderMarkdown("$\\href{javascript:alert(1)}{x}$ $<img src=x onerror=alert(1)>$"));
    // The source may show up as text; it must never become an element or a link.
    expect(root.querySelector("img, [onerror], [href]")).toBeNull();
  });

  it("keeps card text from restyling or imitating the app", () => {
    const root = dom(
      renderMarkdown(
        '<div style="position:fixed;inset:0" class="banner" id="main">x</div><mark class="cloze">c</mark><mark class="cloze evil">d</mark>',
      ),
    );
    const div = root.querySelector("div");
    expect(div?.getAttribute("style")).toBeNull();
    expect(div?.getAttribute("class")).toBeNull();
    expect(div?.getAttribute("id")).toBeNull();
    const marks = root.querySelectorAll("mark");
    expect(marks[0]?.getAttribute("class")).toBe("cloze");
    expect(marks[1]?.getAttribute("class")).toBeNull();
  });

  it("keeps KaTeX styling", () => {
    expect(dom(renderMarkdown("$x^2$")).querySelector(".katex [style]")).not.toBeNull();
  });

  it("loads images only from this app or inline data", () => {
    const root = dom(
      renderMarkdown(
        '![a](/api/media/1) ![b](https://tracker.example/p.gif) <img src="data:image/png;base64,AA=="> <img src="http://[bad">',
      ),
    );
    const sources = [...root.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(sources).toEqual(["/api/media/1", null, "data:image/png;base64,AA==", null]);
  });

  it("never puts math markup inside an attribute", () => {
    const root = dom(renderMarkdown("![$x$](/api/media/1) a < b $y$ & c"));
    expect(root.querySelector("img")?.getAttribute("alt")).toBe("MATHPLACEHOLDER0END");
    expect(root.querySelectorAll(".katex")).toHaveLength(1);
    expect(root.textContent).toContain("a < b");
  });

  it("leaves math in tilde fences alone", () => {
    expect(renderMarkdown("~~~\n$x$\n~~~")).not.toContain("katex");
  });

  it("opens links in a new tab without an opener", () => {
    const link = dom(renderMarkdown("[site](https://example.com)")).querySelector("a");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  describe("with links as text", () => {
    const asText = (source: string) => dom(renderMarkdown(source, { linksAsText: true }));

    it("shows the link text and its address, with nothing to click", () => {
      const root = asText("Log in at [your bank](https://evil.example/login) now");
      expect(root.querySelector("a, [href]")).toBeNull();
      expect(root.textContent).toContain("Log in at your bank (https://evil.example/login) now");
    });

    it("shows a bare address once", () => {
      const root = asText("See https://evil.example/x and <https://evil.example/y> and me@evil.example");
      expect(root.querySelector("a")).toBeNull();
      expect(root.textContent).toBe("See https://evil.example/x and https://evil.example/y and me@evil.example\n");
    });

    it("turns raw HTML links and image-map areas into text", () => {
      const root = asText(
        '<a href="https://evil.example"><strong>Bold</strong> link</a><a name="x">anchor</a>' +
          '<img src="/a.png" usemap="#m"><map name="m"><area href="https://evil.example/area" alt="hot"></map>',
      );
      expect(root.querySelector("a, area, [href]")).toBeNull();
      expect(root.querySelector("strong")?.textContent).toBe("Bold");
      expect(root.textContent).toContain("Bold link (https://evil.example)anchor");
      expect(root.textContent).toContain("(https://evil.example/area)");
    });

    it("keeps math inside link text", () => {
      const root = asText("[$x^2$](https://evil.example)");
      expect(root.querySelector("a")).toBeNull();
      expect(root.querySelector(".katex")).not.toBeNull();
      expect(root.textContent).toContain("(https://evil.example)");
    });

    it("leaves links clickable by default", () => {
      expect(dom(renderMarkdown("[site](https://example.com)")).querySelector("a")).not.toBeNull();
      expect(dom(renderMarkdown("[site](https://example.com)", {})).querySelector("a")).not.toBeNull();
    });
  });
});
