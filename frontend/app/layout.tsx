import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Track Anywhere",
  description: "Personal accounting with draft-first capture and strict ledger confirmation"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

