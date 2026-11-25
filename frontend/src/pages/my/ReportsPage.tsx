// 我的研报：我的线。研报文件存服务端目录，后端已按 本浏览器身份（X-User-Id）/ API Key
// 分目录隔离，不同访客互不串号；本地无 Key 的单机模式即你一个人的数据（向后兼容旧路径）。
import { useEffect, useRef, useState } from "react";
import { Download, ShieldAlert, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, downloadReport, type MyReport } from "@/lib/api";
import { Badge, EmptyState, StatePanel, ToneCallout } from "@/components/ui/primitives";

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
  const [loading, setLoading] = useState(true);
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
      })
      .finally(() => {
        if (alive) setLoading(false);
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
      toast(`已上传 ${created.name}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "上传失败";
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  /** 重新读一次列表（删除结果未知时用来核对真实状态）。 */
  async function refreshList() {
    try {
      setItems(await api.myReports());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取研报失败");
    }
  }

  /**
   * 下载。
   *
   * 🔴 必须接住失败：`downloadReport` 在非 2xx 时 `throw`（`api.ts:97`）。
   * 原来是把裸 promise 直接挂在 onClick 上（`void` + 调用，无 catch）——失败时
   * **什么都不发生**：没有文件、没有提示、控制台之外看不出来。用户只会反复点。
   */
  async function download(item: MyReport) {
    try {
      await downloadReport(item.id, item.name);
    } catch (err) {
      const message = err instanceof Error ? err.message : "下载失败";
      setError(message);
      toast.error(message);
    }
  }

  /**
   * 删除。
   *
   * 🔴 失败时**不能**只弹一句「删除失败」就把这一行留在列表里：请求失败有两种可能——
   * ① 没到达服务端（文件还在）；② 服务端已删但响应丢了（文件没了）。我们分不清。
   * 留在列表里会让用户重复点击，而第二次点会得到 404。所以失败后重新读一次列表，
   * 并明说「结果未知，已重新读取核对」——把不确定性交给用户看，而不是替他猜。
   */
  async function remove(item: MyReport) {
    try {
      await api.deleteReport(item.id);
      setItems((prev) => prev.filter((row) => row.id !== item.id));
      toast(`已删除 ${item.name}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "删除失败";
      toast.error(`${message} · 删除结果未知，已重新读取列表核对`);
      await refreshList();
    }
  }

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">AQSP · 我的线</p>
          <div className="aq-title-row">
            <h1>我的研报</h1>
          </div>
          <p className="aq-page-sub">共 {items.length} 份</p>
        </div>
        <Badge tone="neutral">存在服务端 · 按访客隔离</Badge>
      </header>

      <ToneCallout
        tone="warn"
        icon={ShieldAlert}
        title="研报存在服务器目录，但已按你的浏览器身份分目录隔离"
        detail="不同访客互不串号，也不会看到彼此的研报。本地无 Key 的单机模式是你一个人的数据。换浏览器 / 清缓存会当成新访客（数据独立），可用导出备份迁移。"
      />

      <div className="aq-toolbar">
        <input
          ref={inputRef}
          type="file"
          className="aq-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        <span className="aq-chip">
          <Upload aria-hidden="true" />
          {busy ? "上传中…" : "选择文件上传"}
        </span>
      </div>

      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
      {loading ? <StatePanel>正在读取研报列表…</StatePanel> : null}

      {!loading && items.length === 0 ? (
        <EmptyState title="还没有上传研报" detail="选择本地文件即可上传，上传后可在任意设备下载。" />
      ) : null}

      {items.length > 0 ? (
        <div className="aq-table-wrap">
          <table className="aq-table">
            <thead>
              <tr>
                <th>名称</th>
                <th className="aq-num">大小</th>
                <th className="aq-num">上传时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td className="aq-num">{sizeLabel(item.size)}</td>
                  <td className="aq-num">{new Date(item.ts * 1000).toLocaleString("zh-CN")}</td>
                  <td className="aq-num">
                    <div className="aq-row-actions">
                      <button
                        type="button"
                        className="aq-btn aq-btn-icon"
                        onClick={() => void download(item)}
                        title="下载"
                      >
                        <Download aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="aq-btn aq-btn-danger aq-btn-icon"
                        onClick={() => void remove(item)}
                        title="删除"
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
