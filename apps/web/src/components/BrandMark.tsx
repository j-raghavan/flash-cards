// SPDX-License-Identifier: AGPL-3.0-only
// The logo mark: two stacked cards on the brand gradient, in the chosen palette. Decorative only; the
// product name always sits next to it as text.

import { useId } from "react";

export function BrandMark({ size = 28 }: { size?: number }) {
  // A unique gradient id per instance; useId's punctuation is not safe in url(#...).
  const fill = `brand-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={fill} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--brand-1, #06b6d4)" }} />
          <stop offset="0.55" style={{ stopColor: "var(--brand-2, #10b981)" }} />
          <stop offset="1" style={{ stopColor: "var(--brand-3, #84cc16)" }} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${fill})`} />
      <rect
        x="10"
        y="7"
        width="15"
        height="11"
        rx="2.5"
        fill="#ffffff"
        opacity="0.55"
        transform="rotate(8 17.5 12.5)"
      />
      <rect x="7" y="12" width="16" height="12" rx="2.5" fill="#ffffff" />
      <rect x="10" y="16" width="9" height="1.8" rx="0.9" fill="#0f766e" />
      <rect x="10" y="19.5" width="6" height="1.8" rx="0.9" fill="#0f766e" opacity="0.6" />
    </svg>
  );
}
