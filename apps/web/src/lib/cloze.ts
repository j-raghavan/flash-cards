// SPDX-License-Identifier: AGPL-3.0-only
// Cloze deletions: {{c1::answer}} or {{c1::answer::hint}}. One card per number;
// on card N only the c-N deletions are hidden, the others show their answer.
// The pattern matches the server's (apps/api/src/flashcards/domain/notes.py).

const CLOZE = /\{\{c(0*[1-9]\d*)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g;

/** The deletion numbers present in the text, sorted and unique. */
export function clozeNumbers(text: string): number[] {
  const numbers = new Set<number>();
  for (const match of text.matchAll(CLOZE)) {
    numbers.add(Number(match[1]));
  }
  return [...numbers].sort((a, b) => a - b);
}

/** Markdown for one side of cloze card `ordinal`. */
export function clozeSide(text: string, ordinal: number, revealed: boolean): string {
  return text.replace(CLOZE, (_match, num: string, answer: string, hint: string | undefined) => {
    if (Number(num) !== ordinal) {
      return answer;
    }
    if (revealed) {
      return `<mark class="cloze">${answer}</mark>`;
    }
    return `<mark class="cloze cloze-hidden">[${hint ?? "…"}]</mark>`;
  });
}

/** Markdown with every deletion shown and marked, for reviewing a whole note. */
export function clozeRevealAll(text: string): string {
  return text.replace(
    CLOZE,
    (_match, num: string, answer: string) => `<mark class="cloze" title="c${Number(num)}">${answer}</mark>`,
  );
}

/** The next unused deletion number, for the editor's "add deletion" helper. */
export function nextClozeNumber(text: string): number {
  const numbers = clozeNumbers(text);
  return numbers.length === 0 ? 1 : Math.max(...numbers) + 1;
}
