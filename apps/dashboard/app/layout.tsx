import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MoneyMore · 多行业量化组合",
  description: "银行、红利、工业有色、芯片与创业板成长的多行业因子研究、动态配置、影子执行与对账控制台。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
