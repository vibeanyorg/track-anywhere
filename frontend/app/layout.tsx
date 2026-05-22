import type { Metadata } from "next";
import { AuthProvider } from "./components/auth-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Track Anywhere",
  description: "Personal accounting that fits in a notebook, a chat, or the command line."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
