import type { Metadata } from "next";
import "./globals.css";
import { Tabs } from "@/components/Tabs";

export const metadata: Metadata = {
  title: "Lexground",
  description: "Grounded retrieval over EU regulatory law, with evaluation gates in CI",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="masthead">
            <h1>Lexground</h1>
            <p>Grounded retrieval over EU regulatory law, with evaluation gates in CI</p>
            <Tabs />
          </header>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
