// SPDX-License-Identifier: AGPL-3.0-only
import { Link, Route, Routes } from "react-router";

import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { ForgotPasswordPage, LoginPage, ResetPasswordPage, SignupPage, VerifyEmailPage } from "./pages/auth";
import { BrowserPage } from "./pages/BrowserPage";
import { DeckDetailPage } from "./pages/DeckDetailPage";
import { DeckListPage } from "./pages/DeckListPage";
import { GeneratePage } from "./pages/GeneratePage";
import { JobPage } from "./pages/JobPage";
import { EditNotePage, NewNotePage } from "./pages/NoteEditorPage";
import { StudyPage } from "./pages/StudyPage";

function NotFound() {
  return (
    <section>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Back to your decks</Link>
      </p>
    </section>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route element={<Layout />}>
        <Route index element={<DeckListPage />} />
        <Route path="/decks/:deckId" element={<DeckDetailPage />} />
        <Route path="/decks/:deckId/study" element={<StudyPage />} />
        <Route path="/decks/:deckId/notes/new" element={<NewNotePage />} />
        <Route path="/decks/:deckId/generate" element={<GeneratePage />} />
        <Route path="/notes/:noteId/edit" element={<EditNotePage />} />
        <Route path="/cards" element={<BrowserPage />} />
        <Route path="/jobs/:jobId" element={<JobPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
