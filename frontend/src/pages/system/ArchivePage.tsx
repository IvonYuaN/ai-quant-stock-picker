// 结论归档：系统线。按交易日回看历史结论。
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Archive } from "lucide-react";
import { api } from "@/lib/api";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { cn } from "@/lib/utils";

export function ArchivePage() {
  const [dates, setDates] = useState<string[]>([]);
  const [error, setError] = useState("");
  const { selectedDate, selectDate } = useAqspSnapshot();
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    api
      .aqspDates()
      .then((index) => {
        if (alive) setDates([...index.available_dates].sort().reverse());
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "读取归档失败");
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>结论归档</h1>
          <p>共 {dates.length} 个交易日可选</p>
        </div>
        <span className="vr-chip">公共只读 · 历史结论不修改</span>
      </div>

      {error ? <div className="vr-state-panel vr-state-panel-warning">{error}</div> : null}

      {!error && dates.length === 0 ? (
        <div className="vr-state-panel">还没有可回看的交易日。</div>
      ) : null}

      <div className="vr-date-list">
        {dates.map((date) => (
          <button
            key={date}
            type="button"
            onClick={() => {
              selectDate(date);
              navigate("/system/review");
            }}
            className={cn("vr-date-item", date === selectedDate && "vr-date-item-active")}
          >
            {date}
          </button>
        ))}
      </div>

      {dates.length > 0 ? (
        <p className="vr-private-note" style={{ marginTop: "1rem" }}>
          <Archive className="h-4 w-4 shrink-0" />
          <span>选择日期后会切到「每日复盘」查看该日的结论、证据与分歧。</span>
        </p>
      ) : null}
    </div>
  );
}
