import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import Navbar from "../components/Navbar";
import config from "@/lib/config";

const inter = Inter({ 
  variable: "--font-inter", 
  subsets: ["latin"], 
  display: "swap" 
});

export const metadata = {
  title: "Free AI Social Media Scheduler - MuAPI",
  description: "Schedule and publish AI-generated videos directly to YouTube and TikTok.",
};

export default function RootLayout({ children }) {
  const theme = config?.theme || "slate-indigo";
  const localSession = config?.auth?.localMode
    ? {
        user: { name: "Local User", email: config.auth.localUserEmail || "local@localhost" },
        expires: "2099-01-01T00:00:00.000Z",
      }
    : undefined;

  return (
    <html lang="en" className="h-full w-full" data-theme={theme}>
      <body className={`${inter.variable} ${inter.className} h-full w-full flex flex-col antialiased bg-bg-page text-primary-text overflow-hidden`}>
          <Providers session={localSession}>
          <Navbar />
          <div className="flex-1 flex flex-col overflow-hidden min-h-0">
            {children}
          </div>
        </Providers>
      </body>
    </html>
  );
}

