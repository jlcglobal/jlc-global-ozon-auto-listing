import React from "react";
import ReactDOM from "react-dom/client";
import { App as AntdApp, ConfigProvider, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "@/App";
import "@/styles.css";
import "@/design-system.css";

const FONT_UI =
  'Inter, "Plus Jakarta Sans", "Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif';

ReactDOM.createRoot(document.getElementById("root")!).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      algorithm: antdTheme.defaultAlgorithm,
      token: {
        colorPrimary: "#059669",
        colorInfo: "#059669",
        colorSuccess: "#10b981",
        colorWarning: "#d97706",
        colorError: "#dc2626",
        colorBgLayout: "#f5f7fa",
        colorBgContainer: "#ffffff",
        colorBgElevated: "#ffffff",
        colorText: "#0f172a",
        colorTextSecondary: "#475569",
        colorTextTertiary: "#5b6b80",
        colorBorder: "#e2e8f0",
        colorBorderSecondary: "#f1f5f9",
        borderRadius: 10,
        fontSize: 13,
        fontFamily: FONT_UI,
      },
      components: {
        Layout: { headerBg: "#ffffff", siderBg: "#ffffff", bodyBg: "#f5f7fa", headerPadding: "0 20px" },
        Menu: { itemBg: "#ffffff", itemSelectedBg: "#ecfdf5", itemSelectedColor: "#047857", itemHoverBg: "#f8fafc", itemColor: "#334155", itemBorderRadius: 8 },
        Button: { primaryShadow: "0 1px 2px rgba(15,23,42,0.12)", fontWeight: 500 },
        Card: { colorBgContainer: "#ffffff" },
        Table: { headerBg: "#f8fafc", headerColor: "#475569", rowHoverBg: "#fbfdfc" },
        Tabs: { itemSelectedColor: "#047857", inkBarColor: "#059669" },
      },
    }}
  >
    <AntdApp>
      <App />
    </AntdApp>
  </ConfigProvider>,
);