// 我的笔记：我的线。投研沉淀，只存本地。
import { useState } from "react";
import { Eye, EyeOff, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { addNote, deleteNote, loadNotes, type Note } from "@/lib/notes";
import { Badge, StatePanel } from "@/components/ui/primitives";
import { MarkdownView } from "@/components/ui/Markdown";
import { cn } from "@/lib/utils";

const KINDS = ["复盘", "今日要点", "问AI", "其它"] as const;

export function NotesPage() {
  const [notes, setNotes] = useState<Note[]>(() => loadNotes());
  const [kind, setKind] = useState<string>(KINDS[0]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [renderMarkdown, setRenderMarkdown] = useState(true);

  function save() {
    if (!title.trim() && !content.trim()) {
      toast("标题和正文都是空的");
      return;
    }
    setNotes(addNote(kind, title.trim() || "未命名", content));
    setTitle("");
    setContent("");
    toast("已保存到本地");
  }

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">AQSP · 我的线</p>
          <div className="aq-title-row">
            <h1>我的笔记</h1>
          </div>
          <p className="aq-page-sub">共 {notes.length} 条 · 最多保留最近 200 条</p>
        </div>
        <Badge tone="neutral">只存此浏览器</Badge>
      </header>

      <p className="aq-privacy-note">
        <ShieldCheck aria-hidden="true" />
        <span>笔记保存在本地浏览器，不上传服务器。</span>
      </p>

      <div className="aq-toolbar aq-toolbar-wrap">
        {KINDS.map((item) => (
          <button
            key={item}
            type="button"
            className={cn("aq-btn", kind === item && "aq-btn-primary")}
            onClick={() => setKind(item)}
          >
            {item}
          </button>
        ))}
      </div>

      <input
        className="aq-input aq-input-block"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="标题，例如「每日复盘 2026-09-11」"
      />

      <textarea
        className="aq-input aq-textarea"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="正文，支持 Markdown：## 标题、- 列表、| 表格 |、**加粗**"
      />

      <div className="aq-toolbar">
        <button type="button" className="aq-btn aq-btn-primary" onClick={save}>
          保存
        </button>
        <button
          type="button"
          className="aq-btn"
          onClick={() => setRenderMarkdown((value) => !value)}
          title="切换 Markdown 渲染"
        >
          {renderMarkdown ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
          {renderMarkdown ? "渲染中" : "纯文本"}
        </button>
      </div>

      {notes.length === 0 ? (
        <StatePanel>还没有笔记，写下第一条吧。</StatePanel>
      ) : (
        <div className="aq-note-list">
          {notes.map((note) => (
            <article className="aq-note-card" key={note.id}>
              <div className="aq-note-head">
                <div className="aq-note-title">
                  <strong>{note.title}</strong>
                  <Badge tone="neutral">{note.kind}</Badge>
                </div>
                <div className="aq-note-actions">
                  <span className="aq-time">{new Date(note.ts).toLocaleString("zh-CN")}</span>
                  <button
                    type="button"
                    className="aq-btn aq-btn-danger aq-btn-icon"
                    onClick={() => {
                      setNotes(deleteNote(note.id));
                      toast("已删除");
                    }}
                    title="删除"
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </div>
              </div>
              {renderMarkdown ? (
                <MarkdownView content={note.content} className="aq-note-body" />
              ) : (
                <div className="aq-note-body">{note.content}</div>
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
