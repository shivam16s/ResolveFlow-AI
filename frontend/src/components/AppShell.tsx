"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLandingPage = pathname === "/";

  if (isLandingPage) {
    return <main className="min-h-screen">{children}</main>;
  }

  return (
    <>
      <Sidebar />
      <TopBar />
      <main
        className="pt-14 min-h-screen lg:ml-64"
        style={{
          background:
            "radial-gradient(circle at 52% -10%, rgba(0,210,255,0.08), transparent 34%), linear-gradient(180deg, #0c0c0c 0%, #090909 100%)",
        }}
      >
        {children}
      </main>
    </>
  );
}
