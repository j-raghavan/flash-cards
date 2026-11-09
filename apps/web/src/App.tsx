// SPDX-License-Identifier: AGPL-3.0-only
import { lazy, Suspense, type ReactNode } from "react";
import { Link, Route, Routes } from "react-router";

import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { ForgotPasswordPage, LoginPage, ResetPasswordPage, SignupPage, VerifyEmailPage } from "./pages/auth";
import { BrowserPage } from "./pages/BrowserPage";
import { DeckDetailPage } from "./pages/DeckDetailPage";
import { DeckListPage } from "./pages/DeckListPage";
import { GeneratePage } from "./pages/GeneratePage";
import { GoalPage, GoalsPage } from "./pages/GoalsPage";
import { ImportPage } from "./pages/ImportPage";
import { SharedDeckPage, SharePage } from "./pages/SharePage";
import { JobPage } from "./pages/JobPage";
import { EditNotePage, NewNotePage } from "./pages/NoteEditorPage";
import { useDocumentTitle } from "./lib/useDocumentTitle";
import { StudyPage } from "./pages/StudyPage";

// Screens with charts or editors load on first visit, keeping the start small.
const ProgressPage = lazy(() => import("./progress/ProgressPage").then((m) => ({ default: m.ProgressPage })));
const AdminPage = lazy(() => import("./admin/AdminPage").then((m) => ({ default: m.AdminPage })));
const AccountPage = lazy(() => import("./account/AccountPage").then((m) => ({ default: m.AccountPage })));
const noteTypePages = () => import("./noteTypes/NoteTypesPage");
const NoteTypesPage = lazy(() => noteTypePages().then((m) => ({ default: m.NoteTypesPage })));
const NewNoteTypePage = lazy(() => noteTypePages().then((m) => ({ default: m.NewNoteTypePage })));
const EditNoteTypePage = lazy(() => noteTypePages().then((m) => ({ default: m.EditNoteTypePage })));

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Loading />}>{children}</Suspense>;
}

function NotFound() {
  useDocumentTitle("Page not found");
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
        <Route path="/decks/:deckId/share" element={<SharePage />} />
        <Route path="/shared" element={<SharedDeckPage />} />
        <Route path="/import" element={<ImportPage />} />
        <Route path="/goals" element={<GoalsPage />} />
        <Route path="/goals/:goalId" element={<GoalPage />} />
        <Route
          path="/progress"
          element={
            <Lazy>
              <ProgressPage />
            </Lazy>
          }
        />
        <Route
          path="/note-types"
          element={
            <Lazy>
              <NoteTypesPage />
            </Lazy>
          }
        />
        <Route
          path="/note-types/new"
          element={
            <Lazy>
              <NewNoteTypePage />
            </Lazy>
          }
        />
        <Route
          path="/note-types/:noteTypeId"
          element={
            <Lazy>
              <EditNoteTypePage />
            </Lazy>
          }
        />
        <Route
          path="/admin"
          element={
            <Lazy>
              <AdminPage />
            </Lazy>
          }
        />
        <Route
          path="/account"
          element={
            <Lazy>
              <AccountPage />
            </Lazy>
          }
        />
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
