// SPDX-License-Identifier: AGPL-3.0-only
// The signed-in user's look and a way to change it. A change shows at once and is
// then saved on the account; if saving fails, the previous look comes back and
// the reason is given.

import { useCallback, useState } from "react";

import { account } from "../api/account";
import { describeError } from "../api/client";
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

  const change = useCallback(
    async (part: Partial<Appearance>): Promise<void> => {
      if (user === null) {
        return;
      }
      setError(null);
      signedIn({ ...user, ...part });
      try {
        signedIn({ ...user, ...(await account.setAppearance(part)) });
      } catch (failure) {
        signedIn(user);
        setError(describeError(failure));
      }
    },
    [user, signedIn],
  );

  return { appearance: appearanceOf(user), change, error };
}
