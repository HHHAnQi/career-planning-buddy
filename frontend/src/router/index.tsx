import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { LoginRoute, RequireAuth, RequireDev, RequireProfile } from "@/pages/LoginRoute";

const OnboardingPage = lazy(async () => ({
  default: (await import("@/pages/OnboardingPage")).OnboardingPage,
}));
const TodayPage = lazy(async () => ({
  default: (await import("@/pages/TodayPage")).TodayPage,
}));
const WorkspacePage = lazy(async () => ({
  default: (await import("@/pages/WorkspacePage")).WorkspacePage,
}));
const PlansPage = lazy(async () => ({
  default: (await import("@/pages/PlansPage")).PlansPage,
}));
const PlanDetailPage = lazy(async () => ({
  default: (await import("@/pages/PlanDetailPage")).PlanDetailPage,
}));
const DayPlanPage = lazy(async () => ({
  default: (await import("@/pages/DayPlanPage")).DayPlanPage,
}));
const ReviewsPage = lazy(async () => ({
  default: (await import("@/pages/ReviewsPage")).ReviewsPage,
}));
const MemoriesPage = lazy(async () => ({
  default: (await import("@/pages/MemoriesPage")).MemoriesPage,
}));
const DeveloperRunsPage = lazy(async () => ({
  default: (await import("@/pages/DeveloperRunsPage")).DeveloperRunsPage,
}));
const DeveloperEvalsPage = lazy(async () => ({
  default: (await import("@/pages/DeveloperEvalsPage")).DeveloperEvalsPage,
}));
const DeveloperArchitecturePage = lazy(async () => ({
  default: (await import("@/pages/DeveloperArchitecturePage")).DeveloperArchitecturePage,
}));
const MyPage = lazy(async () => ({
  default: (await import("@/pages/MyPage")).MyPage,
}));
const ProfileSettingsPage = lazy(async () => ({
  default: (await import("@/pages/ProfileSettingsPage")).ProfileSettingsPage,
}));
const InterviewPage = lazy(async () => ({ default: (await import("@/pages/InterviewPage")).InterviewPage }));
const InterviewSetupPage = lazy(async () => ({ default: (await import("@/pages/InterviewSetupPage")).InterviewSetupPage }));
const InterviewRoomPage = lazy(async () => ({ default: (await import("@/pages/InterviewRoomPage")).InterviewRoomPage }));
const InterviewReportPage = lazy(async () => ({ default: (await import("@/pages/InterviewReportPage")).InterviewReportPage }));
const MaterialsPage = lazy(async () => ({ default: (await import("@/pages/MaterialsPage")).MaterialsPage }));
const GrowthPage = lazy(async () => ({ default: (await import("@/pages/GrowthPage")).GrowthPage }));

export const router = createBrowserRouter([
  { path: "/login", element: <LoginRoute /> },
  {
    element: <RequireAuth />,
    children: [
      { path: "/onboarding", element: <OnboardingPage /> },
      {
        element: <RequireProfile />,
        children: [
          {
            element: <AppLayout />,
            children: [
              { path: "/workspace", element: <WorkspacePage /> },
              { path: "/today", element: <TodayPage /> },
              { path: "/interviews", element: <InterviewPage /> },
              { path: "/interviews/new", element: <InterviewSetupPage /> },
              { path: "/interviews/:interviewId", element: <InterviewRoomPage /> },
              { path: "/interviews/:interviewId/report", element: <InterviewReportPage /> },
              { path: "/materials", element: <MaterialsPage /> },
              { path: "/growth", element: <GrowthPage /> },
              { path: "/journey", element: <PlansPage /> },
              { path: "/journey/:planId", element: <PlanDetailPage /> },
              { path: "/journey/:planId/day/:date", element: <DayPlanPage /> },
              { path: "/plans", element: <Navigate to="/journey" replace /> },
              { path: "/plans/:planId", element: <PlanDetailPage /> },
              { path: "/reviews", element: <ReviewsPage /> },
              { path: "/me", element: <MyPage /> },
              { path: "/settings/profile", element: <ProfileSettingsPage /> },
              { path: "/memories", element: <MemoriesPage /> },
              {
                element: <RequireDev />,
                children: [
                  { path: "/dev/runs", element: <DeveloperRunsPage /> },
                  { path: "/dev/evals", element: <DeveloperEvalsPage /> },
                  { path: "/dev/architecture", element: <DeveloperArchitecturePage /> },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
  { path: "/", element: <Navigate to="/workspace" replace /> },
  { path: "*", element: <Navigate to="/workspace" replace /> },
]);
