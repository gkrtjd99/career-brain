import "./index.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { queryClient, setUnauthorizedHandler } from "./lib/api";
import { router } from "./router";

setUnauthorizedHandler(() => {
  const { pathname, href } = router.state.location;
  if (pathname !== "/connect" && pathname !== "/settings") {
    void router.navigate({ to: "/connect", search: { reason: "invalid", next: href } });
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
