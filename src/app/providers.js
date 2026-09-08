"use client";

import { SessionProvider } from "next-auth/react";
import { useEffect } from "react";
import config from "@/lib/config";

export function Providers({ children, session }) {
  useEffect(() => {
    if (typeof window !== "undefined") {
      const theme = config?.theme || "slate-indigo";
      document.documentElement.setAttribute("data-theme", theme);
    }
  }, []);

  return (
    <SessionProvider session={session}>
      {children}
    </SessionProvider>
  );
}
