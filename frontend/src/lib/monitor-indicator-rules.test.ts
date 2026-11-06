import { api, type PriceObservationRuleInput } from "./api";

const originalFetch = globalThis.fetch;
const requests: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
globalThis.fetch = async (input, options) => {
  requests.push({
    url: String(input),
    method: options?.method || "GET",
    body: options?.body ? JSON.parse(String(options.body)) as Record<string, unknown> : {},
  });
  return new Response(JSON.stringify({ data: { id: "indicator-rule" } }), { status: 200 });
};

const indicatorInput: PriceObservationRuleInput = {
  rule_type: "indicator",
  symbol: "600000",
  metric: "rsi12",
  comparator: "at_or_above",
  threshold: 70,
  severity: "warn",
  cooldown_minutes: 15,
  note: "观察超买状态",
};
const priceInput: PriceObservationRuleInput = {
  rule_type: "price",
  symbol: "000001",
  conditions: [{ comparator: "at_or_below", price: 10 }],
  combination: "all",
  severity: "info",
  cooldown_minutes: 0,
  note: "",
};
let indicatorWrite: typeof requests[number] | undefined;
let priceWrite: typeof requests[number] | undefined;
try {
  await api.createPriceObservationRule(indicatorInput);
  indicatorWrite = requests[requests.length - 1];
  await api.createPriceObservationRule(priceInput);
  priceWrite = requests[requests.length - 1];
} finally {
  globalThis.fetch = originalFetch;
}

export const monitorIndicatorRuleTransportContracts = {
  indicatorUsesExistingRuleEndpoint: indicatorWrite?.url === "/api/price-observation-rules" && indicatorWrite.method === "POST",
  indicatorSendsOnlyTypedRsiCondition: indicatorWrite?.body.rule_type === "indicator"
    && indicatorWrite.body.metric === "rsi12"
    && indicatorWrite.body.comparator === "at_or_above"
    && indicatorWrite.body.threshold === 70
    && !("conditions" in indicatorWrite.body),
  indicatorPreservesReviewSettings: indicatorWrite?.body.severity === "warn"
    && indicatorWrite.body.cooldown_minutes === 15
    && indicatorWrite.body.note === "观察超买状态",
  priceRuleTransportRemainsCompatible: priceWrite?.body.rule_type === "price"
    && Array.isArray(priceWrite.body.conditions)
    && priceWrite.body.combination === "all"
    && !("metric" in priceWrite.body),
};
