// react-markdown 的实际渲染器（重依赖，由 Markdown.tsx 按需加载）。
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 不传 rehypeRaw：当前内容只来自本机 localStorage，但关闭 raw HTML
// 可以避免将来接入外部内容时变成注入面。
export default function MarkdownRenderer({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>;
}
