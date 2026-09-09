// 我的研报：我的线。研报文件存服务端目录，后端已按 本浏览器身份（X-User-Id）/ API Key
// 分目录隔离，不同访客互不串号；本地无 Key 的单机模式即你一个人的数据（向后兼容旧路径）。
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, Trash2, Upload } from "lucide-react";
import { api, downloadReport, type MyReport } from "@/lib/api";

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
}

function sizeLabel(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function ReportsPage() {
  const [items, setItems] = useState<MyReport[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    api
      .myReports()
      .then((data) => {
        if (alive) setItems(data);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "读取研报失败");
      });
    return () => {
      alive = false;
    };
  }, []);

  async function upload(file: File) {
    setBusy(true);
    setError("");
    try {
      const content = await readAsBase64(file);
      const created = await api.uploadReport(file.name, content);
      setItems((prev) => [created, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>我的研报</h1>
          <p>共 {items.length} 份</p>
        </div>
        <span className="vr-chip">存在服务端</span>
      </div>

      <div className="vr-gate-block">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span>
          研报存在服务器目录，但已按你的浏览器身份（X-User-Id）分目录隔离——
          不同访客互不串号，也不会看到彼此的研报。本地无 Key 的单机模式是你一个人的数据。
          换浏览器 / 清缓存会当成新访客（数据独立），可用导出备份迁移。
        </span>
      </div>

      <div className="vr-row" style={{ marginBottom: "1rem" }}>
        <input
          ref={inputRef}
          type="file"
          className="vr-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        <span className="vr-chip">
          <Upload className="h-3 w-3" />
          {busy ? "上传中" : "选择文件上传"}
        </span>
      </div>

      {error ? <div className="vr-state-panel vr-state-panel-warning">{error}</div> : null}

      {items.length === 0 ? (
        <div className="vr-state-panel">还没有上传研报。</div>
      ) : (
        <table className="vr-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>大小</th>
              <th>上传时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.name}</td>
                <td>{sizeLabel(item.size)}</td>
                <td>{new Date(item.ts * 1000).toLocaleString("zh-CN")}</td>
                <td>
                  <div className="vr-row">
                    <button
                      type="button"
                      className="vr-button"
                      onClick={() => void downloadReport(item.id, item.name)}
                      title="下载"
                    >
                      <Download className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className="vr-button vr-button-danger"
                      onClick={() => {
                        void api.deleteReport(item.id).then(() => {
                          setItems((prev) => prev.filter((row) => row.id !== item.id));
                        });
                      }}
                      title="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
