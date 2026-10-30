import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

interface OutlineItem {
  id: string;
  label: string;
}

/** 页面目录只表示内容位置，不把阅读顺序伪装成已完成的操作步骤。 */
export function PageOutline({ items }: { items: readonly OutlineItem[] }) {
  const { pathname, search, hash } = useLocation();
  const ids = items.map((item) => item.id).join("|");
  const reveal = (id: string) => {
    const target = document.getElementById(id);
    if (!target) return;
    let node: HTMLElement | null = target;
    while (node) {
      if (node instanceof HTMLDetailsElement) node.open = true;
      node = node.parentElement;
    }
    target.scrollIntoView({ block: "start" });
  };
  useEffect(() => {
    if (!ids.split("|").includes(hash.slice(1))) return;
    const frame = requestAnimationFrame(() => reveal(hash.slice(1)));
    return () => cancelAnimationFrame(frame);
  }, [hash, ids]);
  return <nav className="aq-page-outline" aria-label="本页内容">
    <span className="aq-page-outline-label">本页内容</span>
    <ol>{items.map((item, index) => <li key={item.id}>
      <Link to={{ pathname, search, hash: `#${item.id}` }} aria-current={hash === `#${item.id}` ? "location" : undefined} onClick={() => reveal(item.id)}>
        <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{item.label}
      </Link>
    </li>)}</ol>
  </nav>;
}
