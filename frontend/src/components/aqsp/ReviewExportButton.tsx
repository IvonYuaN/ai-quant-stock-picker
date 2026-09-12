// 复盘导出按钮：把当天结论整理成 Markdown，复制或下载。
//
// 复盘的价值在沉淀 —— 结论散在页面上过几天就只剩印象，落到结构化文本才能存档与回看。
// 两个动作都做了能力探测：剪贴板在非安全上下文/无权限时会失败，这时退回到下载；
// 两个都不可用才报错，而不是闷声失败。
import { useState } from "react";
import { ClipboardCopy } from "lucide-react";
import { toast } from "sonner";
import type { DailyView } from "@/lib/daily-view";
import { buildReviewMarkdown, reviewFileName } from "@/lib/review-export";

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 非安全上下文或无剪贴板权限 */
  }
  return false;
}

function downloadTextFile(name: string, text: string): boolean {
  try {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    if (typeof URL === "undefined" || !URL.createObjectURL) return false;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL?.(url);
    return true;
  } catch {
    return false;
  }
}

export function ReviewExportButton({ view }: { view: DailyView }) {
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const markdown = buildReviewMarkdown(view);
      if (await copyToClipboard(markdown)) {
        toast.success("复盘 Markdown 已复制到剪贴板");
      } else if (downloadTextFile(reviewFileName(view), markdown)) {
        toast("已下载复盘 Markdown 文件");
      } else {
        toast.error("复制与下载都不可用，请检查浏览器权限");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className="aq-btn"
      onClick={() => void run()}
      disabled={busy}
      title="把当天结论、门禁与候选导出成 Markdown"
    >
      <ClipboardCopy aria-hidden="true" />
      导出复盘
    </button>
  );
}
