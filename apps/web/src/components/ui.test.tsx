// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Dialog } from "./ui";

function Harness({ withField = true }: { withField?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
      >
        Open
      </button>
      {open ? (
        <Dialog
          title="Settings"
          onClose={() => {
            setOpen(false);
          }}
        >
          {withField ? <input aria-label="Name" /> : null}
        </Dialog>
      ) : null}
    </>
  );
}

describe("Dialog", () => {
  it("takes focus, keeps Tab inside, closes with Escape and gives focus back", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Settings" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    const close = screen.getByRole("button", { name: "Close" });
    const name = screen.getByLabelText("Name");
    await user.tab({ shift: true });
    expect(name).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(name).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();
    await user.keyboard("a");
    expect(dialog).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("closes with its button", async () => {
    render(<Harness withField={false} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Open" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
