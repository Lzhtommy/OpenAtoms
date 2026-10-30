import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, IBM_Plex_Serif } from "next/font/google";
import { Providers } from "@/components/shell/providers";
import "./globals.css";

const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-sans" });
const serif = IBM_Plex_Serif({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-serif" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: "OpenAtoms — AI team that builds your apps",
  description: "Multi-agent full-stack app builder",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh" className={`${sans.variable} ${serif.variable} ${mono.variable} light`} suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem('oa_theme')==='dark'){document.documentElement.classList.add('dark');document.documentElement.classList.remove('light')}}catch(e){}`,
          }}
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
