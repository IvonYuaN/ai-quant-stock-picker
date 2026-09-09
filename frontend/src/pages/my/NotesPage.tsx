// 我的笔记：我的线。投研沉淀，只存本地。
import { useState } from "react";
import { ShieldCheck, Trash2 } from "lucide-react";
import { addNote, deleteNote, loadNotes, type Note } from "@/lib/notes";

const KINDS = ["复盘", "今日要点", "问AI", "其它"] as const;

export function NotesPage() {
  const [notes, setNotes] = useState<Note[]>(() => loadNotes());
  const [kind, setKind] = useState<string>(KINDS[0]);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>我的笔记</h1>
          <p>共 {notes.length} 条</p>
        </div>
        <span className="vr-chip">只存此浏览器</span>
      </div>

      <p className="vr-private-note">
        <ShieldCheck className="h-4 w-4 shrink-0" />
        <span>笔记保存在本地浏览器，不上传服务器。最多保留最近 200 条。</span>
      </p>

      <div className="vr-row" style={{ marginBottom: "0.75rem" }}>
        {KINDS.map((item) => (
          <button
            key={item}
            type="button"
            className={kind === item ? "vr-button vr-button-primary" : "vr-button"}
            onClick={() => setKind(item)}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="vr-row" style={{ marginBottom: "1rem" }}>
        <input
          className="vr-input"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="标题，例如「每日复盘 2026-09-09」"
        />
      </div>

      <textarea
        className="vr-input"
        style={{ width: "100%", minHeight: "7rem" }}
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="正文，支持纯文本记录"
      />

      <div className="vr-row" style={{ marginTop: "0.75rem", marginBottom: "1.25rem" }}>
        <button
          type="button"
          className="vr-button vr-button-primary"
          onClick={() => {
            if (!title.trim() && !content.trim()) return;
            setNotes(addNote(kind, title.trim() || "未命名", content));
            setTitle("");
            setContent("");
          }}
        >
          保存
        </button>
      </div>

      {notes.length === 0 ? (
        <div className="vr-state-panel">还没有笔记。</div>
      ) : (
        <div style={{ display: "grid", gap: "0.6rem" }}>
          {notes.map((note) => (
            <article className="vr-note-card" key={note.id}>
              <div className="vr-note-head">
                <div>
                  <strong>{note.title}</strong>
                  <span className="vr-chip" style={{ marginLeft: "0.5rem" }}>
                    {note.kind}
                  </span>
                </div>
                <div className="vr-row">
                  <span className="vr-chip">{new Date(note.ts).toLocaleString("zh-CN")}</span>
                  <button
                    type="button"
                    className="vr-button vr-button-danger"
                    onClick={() => setNotes(deleteNote(note.id))}
                    title="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="vr-note-body">{note.content}</div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
