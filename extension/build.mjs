// Сборка: esbuild -> dist/. dist коммитится, чтобы расширение ставилось «Load unpacked» без сборки.
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
await build({
  entryPoints: { popup: "src/popup/main.js", background: "src/background.js" },
  bundle: true, format: "esm", target: "chrome110", outdir: "dist", minify: true, legalComments: "none",
});
cpSync("manifest.json", "dist/manifest.json");
cpSync("src/popup/popup.html", "dist/popup.html");
cpSync("src/popup/popup.css", "dist/popup.css");
for (const s of [16, 48, 128]) cpSync(`icons/icon${s}.png`, `dist/icon${s}.png`);
console.log("built -> dist/");
