// SPDX-License-Identifier: AGPL-3.0-only
// A small born-digital PDF built in memory, so no binary fixture is checked in.

/** A valid PDF with one page per entry of `pages`, each carrying its text as real text. */
export function textPdf(pages: string[]): Buffer {
  const objects: string[] = [];
  // 1: catalog, 2: page tree, 3: font, then a page and its content stream per page.
  const pageRefs = pages.map((_, index) => `${String(4 + index * 2)} 0 R`);
  objects.push("<< /Type /Catalog /Pages 2 0 R >>");
  objects.push(`<< /Type /Pages /Kids [${pageRefs.join(" ")}] /Count ${String(pages.length)} >>`);
  objects.push("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  pages.forEach((text, index) => {
    const safe = text.replace(/[\\()]/g, (c) => `\\${c}`);
    const stream = `BT /F1 18 Tf 72 720 Td (${safe}) Tj ET`;
    objects.push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents ${String(5 + index * 2)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>`,
    );
    objects.push(`<< /Length ${String(stream.length)} >>\nstream\n${stream}\nendstream`);
  });

  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${String(index + 1)} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${String(objects.length + 1)}\n0000000000 65535 f \n`;
  for (const offset of offsets) {
    body += `${String(offset).padStart(10, "0")} 00000 n \n`;
  }
  body += `trailer\n<< /Size ${String(objects.length + 1)} /Root 1 0 R >>\nstartxref\n${String(xref)}\n%%EOF\n`;
  return Buffer.from(body, "latin1");
}
