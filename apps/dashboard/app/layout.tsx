import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MoneyMore · 个人量化控制台",
  description: "招商银行趋势策略的研究、任务、撮合与对账控制台。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
