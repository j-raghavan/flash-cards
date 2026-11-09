// SPDX-License-Identifier: AGPL-3.0-only
// The API returns decks as a flat list with parent_id; the UI shows a tree.

import type { Deck } from "../api/types";

export interface DeckNode {
  deck: Deck;
  depth: number;
  children: DeckNode[];
}

const byName = (a: DeckNode, b: DeckNode): number =>
  a.deck.name.localeCompare(b.deck.name, undefined, { sensitivity: "base" });

/** Roots sorted by name. A deck whose parent is missing is treated as a root. */
export function buildDeckTree(decks: Deck[]): DeckNode[] {
  const nodes = new Map<string, DeckNode>();
  for (const deck of decks) {
    nodes.set(deck.id, { deck, depth: 0, children: [] });
  }
  const roots: DeckNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.deck.parent_id === null ? undefined : nodes.get(node.deck.parent_id);
    if (parent === undefined) {
      roots.push(node);
    } else {
      parent.children.push(node);
    }
  }
  const finish = (list: DeckNode[], depth: number): void => {
    list.sort(byName);
    for (const node of list) {
      node.depth = depth;
      finish(node.children, depth + 1);
    }
  };
  finish(roots, 0);
  return roots;
}

/** The tree flattened in display order, each deck with its depth. */
export function flattenTree(roots: DeckNode[]): DeckNode[] {
  const out: DeckNode[] = [];
  const walk = (list: DeckNode[]): void => {
    for (const node of list) {
      out.push(node);
      walk(node.children);
    }
  };
  walk(roots);
  return out;
}

/** Ancestors of a deck from the root down, excluding the deck itself. */
export function deckPath(decks: Deck[], deckId: string): Deck[] {
  const byId = new Map(decks.map((d) => [d.id, d]));
  const path: Deck[] = [];
  const seen = new Set<string>([deckId]);
  let current = byId.get(deckId)?.parent_id ?? null;
  while (current !== null && !seen.has(current)) {
    seen.add(current);
    const deck = byId.get(current);
    if (deck === undefined) {
      break;
    }
    path.unshift(deck);
    current = deck.parent_id;
  }
  return path;
}

/** How many decks sit below this one, at any depth. */
export function descendantCount(node: DeckNode): number {
  return node.children.reduce((sum, child) => sum + 1 + descendantCount(child), 0);
}

/** Option labels for a deck picker, indented to show nesting. */
export function deckOptions(decks: Deck[]): { id: string; label: string }[] {
  return flattenTree(buildDeckTree(decks)).map((node) => ({
    id: node.deck.id,
    label: `${"  ".repeat(node.depth)}${node.deck.name}`,
  }));
}
