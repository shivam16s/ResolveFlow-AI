import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";

export const metadata: Metadata = {
  title: "ResolveFlow AI — Admin Dashboard",
  description: "AI-powered telecom support operations dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full" style={{ background: "var(--bg)" }}>
        <Sidebar />
        <TopBar />
        <main
          className="ml-64 pt-14 min-h-screen"
          style={{ background: "var(--bg)" }}
        >
          {children}
        </main>
      </body>
    </html>
  );
}
