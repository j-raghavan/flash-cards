// SPDX-License-Identifier: AGPL-3.0-only
// Whether a key press belongs to a text control, so page-wide shortcuts leave it alone.

/** A choice control (a radio or checkbox) takes no text, so shortcuts still apply. */
export function isChoice(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement && (target.type === "radio" || target.type === "checkbox");
}

export function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    !isChoice(target) &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}
