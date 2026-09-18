import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Voice Booking Agent",
  description: "Porter-style AI voice booking assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
