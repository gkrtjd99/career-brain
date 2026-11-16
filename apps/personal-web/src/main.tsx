import "./index.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { queryClient, setUnauthorizedHandler } from "./lib/api";
import { router } from "./router";

setUnauthorizedHandler(() => {
  if (router.state.location.pathname !== "/settings") {
    void router.navigate({ to: "/settings", search: { reason: "invalid" } });
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
