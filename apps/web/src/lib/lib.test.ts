// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it, vi } from "vitest";

import type { Deck } from "../api/types";
import { cardSides } from "./cardSides";
import { clozeNumbers, clozeRevealAll, clozeSide, nextClozeNumber } from "./cloze";
import { buildDeckTree, deckOptions, deckPath, descendantCount, flattenTree } from "./deckTree";
import { forgetAllJobs, recentJobs, rememberJob } from "./recentJobs";
import { newIdempotencyKey, parseTags, safeNextPath, tokenFromLocation } from "./text";

const deck = (id: string, name: string, parent: string | null = null): Deck => ({
  id,
  name,
  parent_id: parent,
  created_at: "2026-01-01T00:00:00Z",
});

describe("cloze", () => {
  const text = "{{c1::Paris}} is in {{c02::France::country}} and {{c1::Île-de-France}}";

  it("finds the deletion numbers", () => {
    expect(clozeNumbers(text)).toEqual([1, 2]);
    expect(clozeNumbers("none")).toEqual([]);
    expect(nextClozeNumber(text)).toBe(3);
    expect(nextClozeNumber("")).toBe(1);
  });

  it("hides only the current deletion, with its hint", () => {
    expect(clozeSide(text, 2, false)).toBe(
      'Paris is in <mark class="cloze cloze-hidden">[country]</mark> and Île-de-France',
    );
    expect(clozeSide(text, 1, false)).toContain('<mark class="cloze cloze-hidden">[…]</mark> is in France');
    expect(clozeSide(text, 2, true)).toContain('<mark class="cloze">France</mark>');
  });

  it("reveals every deletion for review", () => {
    expect(clozeRevealAll("{{c1::a}} {{c2::b::h}}")).toBe(
      '<mark class="cloze" title="c1">a</mark> <mark class="cloze" title="c2">b</mark>',
    );
  });
});

describe("cardSides", () => {
  it("uses front and back for basic notes", () => {
    expect(cardSides("basic", { front: "Q", back: "A" }, 0)).toEqual({ front: "Q", back: "A" });
    expect(cardSides("basic", {}, 0)).toEqual({ front: "", back: "" });
  });

  it("builds cloze sides and appends the extra field", () => {
    expect(cardSides("cloze", { text: "{{c1::x}}" }, 1)).toEqual({
      front: '<mark class="cloze cloze-hidden">[…]</mark>',
      back: '<mark class="cloze">x</mark>',
    });
    expect(cardSides("cloze", { text: "{{c1::x}}", extra: "more" }, 1).back).toContain("---\n\nmore");
    expect(cardSides("cloze", {}, 1)).toEqual({ front: "", back: "" });
  });

  it("fills a custom template, with {{FrontSide}} on the back", () => {
    const template = {
      ordinal: 0,
      name: "Card 1",
      front: "{{Word}} ({{Missing}})",
      back: "{{FrontSide}}\n\n{{Meaning}}",
    };
    expect(cardSides("custom", { Word: "chat", Meaning: "cat" }, 0, template)).toEqual({
      front: "chat ()",
      back: "chat ()\n\ncat",
    });
    expect(cardSides("custom", { Word: "chat" }, 0, null)).toEqual({ front: "", back: "" });
  });

  it("uses the header and back extra of an image occlusion note", () => {
    expect(cardSides("image_occlusion", { header: "Heart", back_extra: "Atria" }, 1)).toEqual({
      front: "Heart",
      back: "Atria",
    });
    expect(cardSides("image_occlusion", {}, 1)).toEqual({ front: "", back: "" });
  });
});

describe("deckTree", () => {
  const decks = [
    deck("b", "beta"),
    deck("a", "Alpha"),
    deck("a2", "zeta", "a"),
    deck("a1", "eta", "a"),
    deck("x", "orphan", "gone"),
    deck("a1a", "deep", "a1"),
  ];

  it("nests and sorts decks, treating orphans as roots", () => {
    const tree = buildDeckTree(decks);
    expect(tree.map((n) => n.deck.name)).toEqual(["Alpha", "beta", "orphan"]);
    expect(flattenTree(tree).map((n) => `${n.depth}:${n.deck.name}`)).toEqual([
      "0:Alpha",
      "1:eta",
      "2:deep",
      "1:zeta",
      "0:beta",
      "0:orphan",
    ]);
    const [first] = tree;
    expect(first && descendantCount(first)).toBe(3);
  });

  it("finds a deck's ancestors and stops at cycles or gaps", () => {
    expect(deckPath(decks, "a1a").map((d) => d.id)).toEqual(["a", "a1"]);
    expect(deckPath(decks, "x")).toEqual([]);
    expect(deckPath(decks, "missing")).toEqual([]);
    const cycle = [deck("p", "p", "q"), deck("q", "q", "p")];
    expect(deckPath(cycle, "p").map((d) => d.id)).toEqual(["q"]);
  });

  it("indents picker options", () => {
    expect(deckOptions([deck("a", "A"), deck("b", "B", "a")])).toEqual([
      { id: "a", label: "A" },
      { id: "b", label: "  B" },
    ]);
  });
});

describe("text helpers", () => {
  it("parses tags", () => {
    expect(parseTags(" bio, Bio  ch::1\nx ")).toEqual(["bio", "ch::1", "x"]);
    expect(parseTags("")).toEqual([]);
  });

  it("reads link tokens from the fragment, then the query", () => {
    expect(tokenFromLocation("#token=abc%2B", "")).toBe("abc+");
    expect(tokenFromLocation("#token=", "?token=q")).toBe("q");
    expect(tokenFromLocation("", "")).toBe("");
  });

  it("allows only same-app paths after sign-in", () => {
    expect(safeNextPath("/decks/1?x=1")).toBe("/decks/1?x=1");
    expect(safeNextPath(null)).toBe("/");
    expect(safeNextPath("https://evil.example")).toBe("/");
    expect(safeNextPath("//evil.example")).toBe("/");
    expect(safeNextPath("/\\evil.example")).toBe("/");
  });

  it("makes unique idempotency keys", () => {
    expect(newIdempotencyKey()).not.toBe(newIdempotencyKey());
  });
});

describe("recentJobs", () => {
  const job = (n: number, deckId = "d1") => ({
    jobId: `j${n}`,
    deckId,
    fileName: `f${n}.pdf`,
    startedAt: "2026-01-01T00:00:00Z",
  });

  it("keeps the latest five jobs per deck, newest first", () => {
    for (let n = 1; n <= 7; n += 1) {
      rememberJob(job(n));
    }
    rememberJob(job(9, "d2"));
    rememberJob(job(7));
    expect(recentJobs("d1").map((j) => j.jobId)).toEqual(["j7", "j6", "j5", "j4", "j3"]);
    expect(recentJobs("d2").map((j) => j.jobId)).toEqual(["j9"]);
    forgetAllJobs();
    expect(recentJobs("d1")).toEqual([]);
  });

  it("survives bad or unavailable storage", () => {
    localStorage.setItem("flashcards.recentJobs", "{not json");
    expect(recentJobs("d1")).toEqual([]);
    localStorage.setItem("flashcards.recentJobs", '{"a":1}');
    expect(recentJobs("d1")).toEqual([]);
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => {
      rememberJob(job(1));
    }).not.toThrow();
    expect(() => {
      forgetAllJobs();
    }).not.toThrow();
    set.mockRestore();
    remove.mockRestore();
  });
});
