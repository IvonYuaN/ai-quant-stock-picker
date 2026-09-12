// Markdown 渲染。
//
// 项目早就装了 react-markdown + remark-gfm，但笔记一直按纯文本显示（pre-wrap），
// 写表格、列表、引用全被压成一行。
//
// react-markdown 连带 micromark 一套解析器有 ~150 kB，而它只在「我的笔记」用到 ——
// 所以按需加载：加载完成前先显示原文（Suspense fallback），加载后替换成渲染结果。
// 这样默认路由（今日研究）不用为它付费。
import { lazy, Suspense } from "react";

const MarkdownRenderer = lazy(() => import("./MarkdownRenderer"));

export function MarkdownView({ content, className }: { content: string; className?: string }) {
  return (
    <div className={className ? `aq-markdown ${className}` : "aq-markdown"}>
      <Suspense fallback={<div className="aq-markdown-raw">{content}</div>}>
        <MarkdownRenderer content={content} />
      </Suspense>
    </div>
  );
}
