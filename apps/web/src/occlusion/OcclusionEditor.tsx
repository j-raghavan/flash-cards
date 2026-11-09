// SPDX-License-Identifier: AGPL-3.0-only
// The Image Occlusion editor (F-CARD-3, ADR 0013): upload an image, then hide
// parts of it with rectangles. Draw a rectangle by dragging over the image, or
// add one with the button and place it with the number fields, which also work
// from the keyboard. Masks that share a card number make one card.
// Loaded on first use, so other screens do not carry it.

import { useState, type PointerEvent } from "react";

import { apiUrl } from "../api/client";
import { media } from "../api/endpoints";
import type { Mask, Occlusion } from "../api/types";
import { ErrorText } from "../components/ui";
import { useSubmit } from "../lib/useSubmit";

export const MAX_MASKS = 100;
export const MAX_MASK_ID = 999;
const MIN_SIZE = 0.01;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const MAX_IMAGE_BYTES = 25 * 1024 * 1024;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** A mask kept inside the image, at least 1% wide and high. */
export function fitMask(mask: Mask): Mask {
  const width = clamp(mask.width, MIN_SIZE, 1);
  const height = clamp(mask.height, MIN_SIZE, 1);
  return {
    id: clamp(Math.round(mask.id), 1, MAX_MASK_ID),
    width,
    height,
    left: clamp(mask.left, 0, 1 - width),
    top: clamp(mask.top, 0, 1 - height),
  };
}

function nextId(masks: Mask[]): number {
  return Math.min(MAX_MASK_ID, Math.max(0, ...masks.map((m) => m.id)) + 1);
}

function percent(value: number): number {
  return Math.round(value * 1000) / 10;
}

const POSITION_FIELDS = [
  { key: "left", label: "Left" },
  { key: "top", label: "Top" },
  { key: "width", label: "Width" },
  { key: "height", label: "Height" },
] as const;

interface Draft {
  x: number;
  y: number;
  toX: number;
  toY: number;
}

function draftMask(draft: Draft, id: number): Mask {
  return {
    id,
    left: Math.min(draft.x, draft.toX),
    top: Math.min(draft.y, draft.toY),
    width: Math.abs(draft.toX - draft.x),
    height: Math.abs(draft.toY - draft.y),
  };
}

/** The image with its masks, drawn by dragging, and the list of masks to edit by hand. */
function Masks({ occlusion, onChange }: { occlusion: Occlusion; onChange: (occlusion: Occlusion) => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const masks = occlusion.masks;
  const full = masks.length >= MAX_MASKS;
  const cards = new Set(masks.map((m) => m.id)).size;

  const setMasks = (next: Mask[]): void => {
    onChange({ ...occlusion, masks: next });
  };

  const point = (event: PointerEvent<HTMLDivElement>): { x: number; y: number } => {
    const box = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp((event.clientX - box.left) / box.width, 0, 1),
      y: clamp((event.clientY - box.top) / box.height, 0, 1),
    };
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>): void => {
    if (full || event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const { x, y } = point(event);
    setDraft({ x, y, toX: x, toY: y });
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>): void => {
    if (draft !== null) {
      const { x, y } = point(event);
      setDraft({ ...draft, toX: x, toY: y });
    }
  };

  const onPointerUp = (): void => {
    if (draft === null) {
      return;
    }
    const mask = draftMask(draft, nextId(masks));
    setDraft(null);
    if (mask.width >= MIN_SIZE && mask.height >= MIN_SIZE) {
      setMasks([...masks, fitMask(mask)]);
      setSelected(masks.length);
    }
  };

  const update = (index: number, changes: Partial<Mask>): void => {
    setMasks(masks.map((mask, i) => (i === index ? fitMask({ ...mask, ...changes }) : mask)));
  };

  const shown = draft === null ? masks : [...masks, draftMask(draft, 0)];

  return (
    <>
      <p className="muted" id="occlusion-hint">
        Drag over the image to hide a part. Each card number becomes one card; masks with the same number are
        hidden together.
      </p>
      <div
        className="occlusion occlusion-surface"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          setDraft(null);
        }}
      >
        <img
          src={apiUrl(`/media/${encodeURIComponent(occlusion.image_id)}`)}
          alt="The image to hide parts of"
          draggable={false}
        />
        {shown.map((mask, index) => (
          <span
            key={index}
            className={index === selected ? "mask mask-own" : "mask"}
            aria-hidden="true"
            style={{
              left: `${String(mask.left * 100)}%`,
              top: `${String(mask.top * 100)}%`,
              width: `${String(mask.width * 100)}%`,
              height: `${String(mask.height * 100)}%`,
            }}
          >
            {mask.id === 0 ? null : <span className="mask-label">{mask.id}</span>}
          </span>
        ))}
      </div>
      <ol className="mask-list" aria-describedby="occlusion-hint">
        {masks.map((mask, index) => (
          <li
            key={index}
            className={index === selected ? "selected" : undefined}
            onFocus={() => {
              setSelected(index);
            }}
          >
            <fieldset className="row">
              <legend>Mask {index + 1}</legend>
              <label className="field">
                <span>Card</span>
                <input
                  type="number"
                  min={1}
                  max={MAX_MASK_ID}
                  value={mask.id}
                  onChange={(event) => {
                    update(index, { id: Number(event.target.value) });
                  }}
                />
              </label>
              {POSITION_FIELDS.map(({ key, label }) => (
                <label className="field" key={key}>
                  <span>{label} (%)</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step={0.5}
                    value={percent(mask[key])}
                    onChange={(event) => {
                      update(index, { [key]: Number(event.target.value) / 100 });
                    }}
                  />
                </label>
              ))}
              <button
                type="button"
                className="button danger-text"
                onClick={() => {
                  setMasks(masks.filter((_m, i) => i !== index));
                  setSelected(null);
                }}
              >
                Remove
              </button>
            </fieldset>
          </li>
        ))}
      </ol>
      <div className="row">
        <button
          type="button"
          className="button"
          disabled={full}
          onClick={() => {
            setMasks([...masks, { id: nextId(masks), left: 0.4, top: 0.45, width: 0.2, height: 0.1 }]);
            setSelected(masks.length);
          }}
        >
          Add a mask
        </button>
        <span className="muted">
          {masks.length === 0
            ? "No masks yet."
            : `${String(cards)} card${cards === 1 ? "" : "s"} from ${String(masks.length)} mask${masks.length === 1 ? "" : "s"}.`}
          {full ? ` At most ${String(MAX_MASKS)} masks.` : ""}
        </span>
      </div>
    </>
  );
}

export function OcclusionEditor({
  occlusion,
  onChange,
}: {
  occlusion: Occlusion | null;
  onChange: (occlusion: Occlusion | null) => void;
}) {
  const upload = useSubmit();

  const chooseImage = (file: File): void => {
    if (!IMAGE_TYPES.includes(file.type)) {
      upload.setError("Choose a PNG, JPEG, GIF or WebP image.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      upload.setError("That image is larger than 25 MB.");
      return;
    }
    void upload.run(async () => {
      const asset = await media.upload(file);
      // A new image keeps the masks: they are relative to its size.
      onChange({ image_id: asset.id, masks: occlusion?.masks ?? [] });
    });
  };

  return (
    <fieldset className="occlusion-editor">
      <legend>Image and hidden parts</legend>
      <label className="field">
        <span>{occlusion === null ? "Image" : "Replace the image"}</span>
        <input
          type="file"
          accept={IMAGE_TYPES.join(",")}
          disabled={upload.busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) {
              chooseImage(file);
            }
          }}
        />
      </label>
      {upload.busy ? (
        <p className="muted" role="status">
          Uploading…
        </p>
      ) : null}
      <ErrorText message={upload.error} />
      {occlusion === null ? null : <Masks occlusion={occlusion} onChange={onChange} />}
    </fieldset>
  );
}
