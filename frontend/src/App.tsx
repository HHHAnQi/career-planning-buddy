import { Suspense } from "react";
import { RouterProvider } from "react-router-dom";

import { router } from "./router";

export function App(): JSX.Element {
  return (
    <Suspense fallback={<main className="p-6">页面加载中…</main>}>
      <RouterProvider router={router} />
    </Suspense>
  );
}
