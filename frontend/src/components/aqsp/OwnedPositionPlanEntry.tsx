import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type Holding } from "@/lib/api";
import { normalizePortfolio } from "@/lib/portfolio-view";
import { ownedPositionForStock, positionPlanHref } from "@/lib/position-workspace";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";

/** Quote/research ownership badges alone never identify a private position. */
export function OwnedPositionPlanEntry({ code }: { code: string | undefined }) {
  const [holding, setHolding] = useState<Holding | null>(null);
  const [verifiedCode, setVerifiedCode] = useState("");
  const generation = useRef(0);
  const location = useLocation();
  useEffect(() => {
    let active = true;
    async function load() {
      const version = ++generation.current;
      try {
        const portfolio = normalizePortfolio(await api.portfolio());
        if (active && generation.current === version) { setHolding(ownedPositionForStock(portfolio.holdings, code ?? "")); setVerifiedCode(code ?? ""); }
      } catch { if (active && generation.current === version) { setHolding(null); setVerifiedCode(""); } }
    }
    void load();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, load);
    return () => { active = false; generation.current++; window.removeEventListener(PORTFOLIO_CHANGED_EVENT, load); };
  }, [code]);
  const href = verifiedCode === code ? positionPlanHref(holding?.position_id, location.pathname + location.search + location.hash) : null;
  return href ? <Link className="aq-btn aq-btn-primary" to={href}>看我的处理计划</Link> : null;
}
