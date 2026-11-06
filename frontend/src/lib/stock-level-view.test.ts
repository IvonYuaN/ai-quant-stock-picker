import { availableStockLevels, STOCK_LEVEL_LABELS, unavailableStockLevels } from "./stock-level-view";
import type { StockPriceLevel } from "@/lib/api";

const levels: StockPriceLevel[] = [
  { type: "classic_pivot", status: "available", formula: "P", reason: "", as_of: "2026-10-30", price_basis: "raw", source: "tencent", levels: [{ label: "P", price: 12 }] },
  { type: "volume_profile", status: "unavailable", formula: "", reason: "missing volume profile", as_of: null, price_basis: "raw", source: "", levels: [] },
];

export const stockLevelViewContracts = {
  keepsAvailableLevelsSeparate: availableStockLevels(levels).length === 1,
  preservesUnsupportedAsUnavailable: unavailableStockLevels(levels)[0]?.type === "volume_profile",
  labelsTickFlowFamilies: STOCK_LEVEL_LABELS.fibonacci === "斐波那契回撤",
};
