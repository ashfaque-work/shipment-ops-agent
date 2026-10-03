import type { Metadata, Viewport } from "next";
import { Big_Shoulders_Stencil, JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

const stencil = Big_Shoulders_Stencil({
  subsets: ["latin"],
  weight: ["700", "800"],
  variable: "--font-stencil",
});
const grotesk = Schibsted_Grotesk({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Shipment Ops Agent",
  description:
    "Track shipments, compare rates, book with confirmation, check shipping documents and draft customer emails.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${stencil.variable} ${grotesk.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
