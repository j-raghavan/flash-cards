// SPDX-License-Identifier: AGPL-3.0-only
// The signed-in user's look and a way to change it. A change shows at once and is
// then saved on the account; if saving fails, the last saved look comes back and
// the reason is given.
//
// Changes can overlap (a double click on the header switch, two quick picks), and
// their answers can arrive out of order. Only the newest change decides what the
// page shows, and every answer is merged into the user as they are now, not as
// they were when the change began.

import { useCallback, useEffect, useRef, useState } from "react";

import { account } from "../api/account";
import { describeError } from "../api/client";
import type { User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { appearanceOf, type Appearance } from "./appearance";

export interface AppearanceChoice {
  appearance: Appearance;
  change: (part: Partial<Appearance>) => Promise<void>;
  error: string | null;
}

export function useAppearance(): AppearanceChoice {
  const { user, signedIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const latestUser = useRef<User | null>(user);
  const latestChange = useRef(0);
  const inFlight = useRef(0);
  // The newest look the server has confirmed, and which change confirmed it.
  // With nothing in flight, that is simply the look the user has now.
  const saved = useRef<{ change: number; appearance: Appearance }>({ change: 0, appearance: appearanceOf(user) });

  useEffect(() => {
    latestUser.current = user;
  }, [user]);

  const change = useCallback(
    async (part: Partial<Appearance>): Promise<void> => {
      const show = (look: Partial<Appearance>): void => {
        const now = latestUser.current;
        if (now !== null) {
          latestUser.current = { ...now, ...look };
          signedIn(latestUser.current);
        }
      };
      const current = latestUser.current;
      if (current === null) {
        return;
      }
      const id = ++latestChange.current;
      if (inFlight.current === 0) {
        saved.current = { change: id - 1, appearance: appearanceOf(current) };
      }
      inFlight.current += 1;
      setError(null);
      show(part);
      try {
        const appearance = await account.setAppearance(part);
        if (id > saved.current.change) {
          saved.current = { change: id, appearance };
        }
        if (id === latestChange.current) {
          show(saved.current.appearance);
        }
      } catch (failure) {
        if (id === latestChange.current) {
          show(saved.current.appearance);
          setError(describeError(failure));
        }
      } finally {
        inFlight.current -= 1;
      }
    },
    [signedIn],
  );

  return { appearance: appearanceOf(user), change, error };
}
