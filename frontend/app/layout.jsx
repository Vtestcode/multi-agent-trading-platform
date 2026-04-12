import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "Multi Agent Equity Trading Platform",
  description: "Operator dashboard for an LLM-driven multi-agent equity trading platform.",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
    apple: "/icon.svg",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        {process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ? (
          <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" />
        ) : null}
        {children}
      </body>
    </html>
  );
}
