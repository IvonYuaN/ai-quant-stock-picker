#!/usr/bin/env node
// 契约断言执行器（frontend/scripts/run-contracts.mjs）
//
// 背景：src/**/*.test.ts 里写的断言，此前**只被 `tsc --noEmit` 做类型检查，布尔表达式从未被求值**。
// 那些文件看起来像测试，实际不验证任何东西 —— 项目 IA 漂移（页签漏掉「候选研究」段）就是这样漏过去的。
//
// 这里用 esbuild（vite 自带依赖）把每个 .test.ts 打成自包含 ESM，import 后递归收集导出的布尔量：
//   - 任一布尔为 false → 失败
//   - 一个布尔都没有 → 失败（说明这个"测试"其实没断言任何东西）
//
// 用法：node scripts/run-contracts.mjs   （或 npm test / npm run contracts）
import { readdir, mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import esbuild from "esbuild";

const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const SRC = join(FRONTEND, "src");

async function findTestFiles(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await findTestFiles(full)));
    else if (entry.name.endsWith(".test.ts")) out.push(full);
  }
  return out.sort();
}

/** 递归收集值里的所有布尔量，带可读路径。 */
function collectBooleans(value, path, out) {
  if (typeof value === "boolean") {
    out.push({ path, value });
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectBooleans(item, `${path}[${index}]`, out));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      collectBooleans(child, path ? `${path}.${key}` : key, out);
    }
  }
}

const files = await findTestFiles(SRC);
if (files.length === 0) {
  console.error("没有找到任何 *.test.ts 契约文件");
  process.exit(1);
}

const workDir = await mkdtemp(join(tmpdir(), "aqsp-contracts-"));
const failures = [];
let totalAssertions = 0;

try {
  for (const file of files) {
    const label = relative(FRONTEND, file);
    let code;
    try {
      const result = await esbuild.build({
        entryPoints: [file],
        bundle: true,
        format: "esm",
        platform: "node",
        write: false,
        logLevel: "silent",
        alias: { "@": SRC },
      });
      code = result.outputFiles[0].text;
    } catch (error) {
      failures.push(`${label}: 打包失败 — ${error.message}`);
      continue;
    }

    const bundlePath = join(workDir, `${label.replace(/[/\\]/g, "_")}.mjs`);
    await writeFile(bundlePath, code);

    let module;
    try {
      module = await import(pathToFileURL(bundlePath).href);
    } catch (error) {
      failures.push(`${label}: 求值抛错 — ${error.message}`);
      continue;
    }

    const booleans = [];
    for (const [name, value] of Object.entries(module)) {
      // 名字里带 fixture 的是**测试数据**，不是断言（例如 meta.historical = false 是刻意构造的）。
      if (/fixture/i.test(name)) continue;
      collectBooleans(value, name, booleans);
    }

    if (booleans.length === 0) {
      failures.push(`${label}: 没有任何布尔断言（文件存在但不验证任何东西）`);
      continue;
    }

    totalAssertions += booleans.length;
    const bad = booleans.filter((item) => !item.value);
    if (bad.length > 0) {
      for (const item of bad) failures.push(`${label}: ${item.path}`);
    } else {
      console.log(`  ok  ${label}  (${booleans.length} 项断言)`);
    }
  }
} finally {
  await rm(workDir, { recursive: true, force: true });
}

console.log("");
console.log(`契约文件 ${files.length} 个 · 断言 ${totalAssertions} 项 · 失败 ${failures.length} 项`);
if (failures.length > 0) {
  console.error("");
  for (const item of failures) console.error(`  FAIL  ${item}`);
  process.exit(1);
}
console.log("全部通过");
