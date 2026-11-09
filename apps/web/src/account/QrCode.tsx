// SPDX-License-Identifier: AGPL-3.0-only
// A QR code drawn in the browser as SVG, so an authenticator secret is never sent
// to a third-party image service.

import qrcode from "qrcode-generator";

const QUIET_ZONE = 4;

export function QrCode({ value, label, size = 200 }: { value: string; label: string; size?: number }) {
  const qr = qrcode(0, "M");
  qr.addData(value);
  qr.make();
  const count = qr.getModuleCount();
  const extent = count + QUIET_ZONE * 2;
  const cells: string[] = [];
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (qr.isDark(row, col)) {
        cells.push(`M${String(col + QUIET_ZONE)} ${String(row + QUIET_ZONE)}h1v1h-1z`);
      }
    }
  }
  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${String(extent)} ${String(extent)}`}
      shapeRendering="crispEdges"
    >
      <rect width={extent} height={extent} fill="#fff" />
      <path d={cells.join("")} fill="#000" />
    </svg>
  );
}
