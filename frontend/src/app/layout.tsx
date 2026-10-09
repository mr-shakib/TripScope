import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "TripScope", template: "%s · TripScope" },
  description: "NYC TLC trip-record analytics: validated pipeline, fast dashboards, grounded insights.",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Render per request so the proxy's CSP nonce is applied to Next.js scripts.
  await connection();
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
      <body className="min-h-screen">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
