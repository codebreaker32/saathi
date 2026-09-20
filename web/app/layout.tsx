import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Saathi — is there a human on this line?",
  description: "The AI waits on hold. You take the call.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
