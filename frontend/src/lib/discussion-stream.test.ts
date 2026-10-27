import { readDiscussionStream } from "./discussion-stream";

function response(parts: Uint8Array[], cancel?: () => void): Response {
  return new Response(new ReadableStream({
    start(controller) { parts.forEach((part) => controller.enqueue(part)); controller.close(); },
    cancel,
  }));
}
const encode = (text: string) => new TextEncoder().encode(text);
const source = encode('{"event":"progress","stage":"challenge"}\n{"event":"result","data":{"text":"中文结果"}}\n');
const stages: string[] = [];
const value = await readDiscussionStream<{ text: string }>(response(Array.from(source, (byte) => Uint8Array.of(byte))), (stage) => stages.push(stage));
async function rejects(parts: Uint8Array[]): Promise<boolean> {
  try { await readDiscussionStream(response(parts), () => undefined); return false; }
  catch { return true; }
}
let cancelled = false;
const streaming = new Response(new ReadableStream({
  start(controller) { controller.enqueue(encode('{"event":"result","data":{"saved":true}}\n')); },
  cancel() { cancelled = true; },
}));
await readDiscussionStream(streaming, () => undefined);
export const streamContracts = {
  fragmentedUtf8: value.text === "中文结果",
  progressSeparateFromResult: stages.join() === "challenge",
  finalLineWithoutNewline: (await readDiscussionStream<{ ok: boolean }>(response([encode('{"event":"result","data":{"ok":true}}')]), () => undefined)).ok,
  missingResultRejected: await rejects([encode('{"event":"progress","stage":"synthesis"}\n')]),
  upstreamErrorRejected: await rejects([encode('{"event":"error","detail":"持仓已变化"}\n')]),
  malformedJsonRejected: await rejects([encode('{broken}\n')]),
  oversizedRejected: await rejects([new Uint8Array(2_000_001)]),
  releasesConnectionAfterResult: cancelled,
};
