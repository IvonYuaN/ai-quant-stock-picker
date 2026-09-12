// 全局命令面板（Cmd/Ctrl+K 或 /）。
//
// 解决的是最基础的一类体验问题：想看一只股票，得先判断它该在哪个页面、再找到它、再点开。
// 这里把「导航 / 自选 / 直接输代码」合成一个入口：想查什么直接敲。
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CornerDownLeft, LineChart, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { NAV_LINES } from "@/lib/ia";
import { loadWatch } from "@/lib/watchlist";

interface Command {
  key: string;
  label: string;
  hint: string;
  group: string;
  run: () => void;
}

const CODE_PATTERN = /^\d{6}$/;

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const commands = useMemo<Command[]>(() => {
    const goto = (to: string) => () => navigate(to);

    const navCommands: Command[] = NAV_LINES.flatMap((line) =>
      line.items.map((item) => ({
        key: `nav:${item.to}`,
        label: item.label,
        hint: `${line.title} · ${item.desc}`,
        group: "跳转",
        run: goto(item.to),
      })),
    );

    const watch = loadWatch();
    const watchCommands: Command[] = watch.map((code) => ({
      key: `watch:${code}`,
      label: code,
      hint: "自选 · 查看个股详情",
      group: "自选",
      run: goto(`/today?symbol=${code}`),
    }));

    return [...navCommands, ...watchCommands];
  }, [navigate]);

  // 直接输 6 位代码永远可用（不必先加自选）
  const trimmed = query.trim();
  const codeCommand: Command | null = CODE_PATTERN.test(trimmed)
    ? {
        key: `code:${trimmed}`,
        label: trimmed,
        hint: "查看个股详情",
        group: "代码",
        run: () => navigate(`/today?symbol=${trimmed}`),
      }
    : null;

  const results = useMemo(() => {
    const pool = codeCommand ? [codeCommand, ...commands] : commands;
    if (!trimmed) return pool.slice(0, 12);
    const needle = trimmed.toLowerCase();
    return pool
      .filter(
        (cmd) =>
          cmd.label.toLowerCase().includes(needle) || cmd.hint.toLowerCase().includes(needle),
      )
      .slice(0, 12);
  }, [commands, codeCommand, trimmed]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      // 面板由键盘唤起，焦点必须落进输入框，否则还得再点一下
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [trimmed]);

  if (!open) return null;

  const commit = (index: number) => {
    const cmd = results[index];
    if (!cmd) return;
    cmd.run();
    onClose();
  };

  return (
    <div
      className="aq-palette-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="aq-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <div className="aq-palette-input">
          <Search aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                onClose();
                return;
              }
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((value) => (results.length ? (value + 1) % results.length : 0));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((value) => (results.length ? (value - 1 + results.length) % results.length : 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                commit(active);
              }
            }}
            placeholder="输入代码、页面名或自选股…"
            aria-label="搜索命令"
          />
        </div>

        {results.length === 0 ? (
          <p className="aq-palette-empty">没有匹配项。直接输 6 位代码可查看个股详情。</p>
        ) : (
          <ul className="aq-palette-list" role="listbox">
            {results.map((cmd, index) => (
              <li key={cmd.key}>
                <button
                  type="button"
                  role="option"
                  aria-selected={index === active}
                  className={cn("aq-palette-item", index === active && "aq-palette-item-active")}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => commit(index)}
                >
                  <LineChart className="aq-palette-item-icon" aria-hidden="true" />
                  <span className="aq-palette-item-label">{cmd.label}</span>
                  <span className="aq-palette-item-hint">{cmd.hint}</span>
                  {index === active ? <CornerDownLeft aria-hidden="true" /> : <ArrowRight aria-hidden="true" />}
                </button>
              </li>
            ))}
          </ul>
        )}

        <p className="aq-palette-foot">
          <kbd>↑</kbd>
          <kbd>↓</kbd> 选择 · <kbd>Enter</kbd> 打开 · <kbd>Esc</kbd> 关闭
        </p>
      </div>
    </div>
  );
}
