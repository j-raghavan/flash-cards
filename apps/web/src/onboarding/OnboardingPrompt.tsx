// SPDX-License-Identifier: AGPL-3.0-only
// The home page's link into the guided first run (F-ONB-1). A user with no decks
// who has never seen the setup on this device is taken to it once; a setup in
// progress is offered again here until it is finished or skipped.

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { ONBOARDING_STEPS, finishOnboarding, readOnboarding, stepNumber } from "./state";

export function OnboardingPrompt({ deckCount }: { deckCount: number }) {
  const { user } = useAuth();
  return user === null ? null : <Prompt userId={user.id} deckCount={deckCount} />;
}

function Prompt({ userId, deckCount }: { userId: string; deckCount: number }) {
  const navigate = useNavigate();
  const [state, setState] = useState(() => readOnboarding(userId));
  const firstRun = state === null && deckCount === 0;

  useEffect(() => {
    if (firstRun) {
      void navigate("/welcome", { replace: true });
    }
  }, [firstRun, navigate]);

  if (state?.status !== "active") {
    return null;
  }
  return (
    <aside className="panel" aria-label="Guided setup">
      <p>
        You are on step {stepNumber(state.step)} of {ONBOARDING_STEPS.length} of the guided setup.
      </p>
      <div className="row">
        <Link className="button primary" to="/welcome">
          Continue setup
        </Link>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            finishOnboarding(userId);
            setState(readOnboarding(userId));
          }}
        >
          Skip the setup
        </button>
      </div>
    </aside>
  );
}
