import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

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
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
