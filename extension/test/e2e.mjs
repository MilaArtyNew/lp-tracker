// E2E: реальный API (Python + Postgres) + собранное расширение в Chrome + подставной window.ethereum вместо Rabby.
// Запуск: DATABASE_URL=postgresql://... PYTHON=/path/to/python npm run build && node test/e2e.mjs
import { chromium } from "playwright-core";
import { spawn } from "node:child_process";
import { mkdtempSync, cpSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import http from "node:http";
import { AbiCoder } from "ethers";
import { CHAINS, provider, positionState } from "../src/core/chain.js";
import { findLivePosition } from "./helpers.mjs";
import { POSM_IFACE, poolIdOf } from "../src/core/v4.js";

const PYTHON = process.env.PYTHON || "python3";
const CHROME = process.env.CHROME_PATH || `${process.env.HOME}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome` /* Chromium for Testing: у брендового Chrome 137+ нет --load-extension */;
const chain = "base", posm = CHAINS[chain].posm;
const results = []; let failed = 0;
const check = (name, ok, extra = "") => { results.push(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  — " + extra : ""}`); console.log(results.at(-1)); if (!ok) failed++; };

// --- реальные данные с Base: живая позиция и успешная tx-хэш для «отправки» ---
const live = await findLivePosition(chain);
if (!live) throw new Error("не нашла живую позицию на Base");
const OWNER = live.st.owner;
let realHash = null;
{
  const head = await provider(chain).getBlockNumber();
  const blk = await provider(chain).getBlock(head - 20, false);
  for (const h of blk.transactions) { const r = await provider(chain).getTransactionReceipt(h); if (r?.status === 1) { realHash = h; break; } }
}
console.log(`позиция #${live.id} владелец ${OWNER}; tx-заглушка ${realHash}`);

// --- сервер API ---
const positions = [
  { id: 0, token_id: String(live.id), chain, protocol: "v4", token0_symbol: "ETH", token1_symbol: "USDC", status: "in_range",
    price_lower: 1500, price_upper: 4500, current_price: 3000, value_usd: 1234.5, fees_usd: 12.3 },
  { token_id: "555", chain: "base", protocol: "v3", token0_symbol: "PEPE", token1_symbol: "ETH", status: "above",
    price_lower: 1, price_upper: 2, current_price: 5, value_usd: 50, fees_usd: 1 },
];
const srv = spawn(PYTHON, [resolve("../tests/e2e_server.py")], { env: { ...process.env, BOT_TOKEN: "x", E2E_WALLET: OWNER, E2E_POSITIONS: JSON.stringify(positions), PYTHONWARNINGS: "ignore" }, stdio: ["ignore", "pipe", "inherit"] });
const boot = await new Promise((res, rej) => { srv.stdout.once("data", (d) => res(JSON.parse(d.toString().trim().split("\n").pop()))); srv.once("exit", (c) => rej(new Error("server exit " + c))); setTimeout(() => rej(new Error("server timeout")), 30000); });

// --- страница «сайта» с подставным кошельком ---
let walletAccount = OWNER;
const site = http.createServer((req, res) => {
  res.setHeader("content-type", "text/html");
  res.end(`<!doctype html><title>mock site</title><script>
    window.__calls = [];
    window.ethereum = { request: async ({ method, params }) => {
      window.__calls.push({ method, params });
      if (method === "eth_requestAccounts") return [${JSON.stringify("__ACC__")}.replace("__ACC__", localStorage.acc || "${OWNER}")];
      if (method === "wallet_switchEthereumChain") return null;
      if (method === "eth_sendTransaction") return "${realHash}";
      throw new Error("unexpected " + method);
    } };</script>ok`);
}).listen(18081);

const pageErrors = []; let popup;
// Копия dist с host-разрешением на тестовый «сайт»: в реальном popup доступ к вкладке даёт activeTab (клик по иконке),
// а в тесте popup открыт как обычная вкладка. Боевой манифест при этом не расширяется.
const extDir = mkdtempSync(join(tmpdir(), "lpext-dist-"));
cpSync(resolve("dist"), extDir, { recursive: true });
const mf = JSON.parse(readFileSync(join(extDir, "manifest.json"), "utf8"));
mf.host_permissions.push("http://127.0.0.1/*");
writeFileSync(join(extDir, "manifest.json"), JSON.stringify(mf));
const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "lpext-")), {
  executablePath: CHROME, headless: false,
  args: ["--headless=new", "--no-sandbox", `--disable-extensions-except=${extDir}`, `--load-extension=${extDir}`],
});
try {
  const sw = ctx.serviceWorkers()[0] || (await ctx.waitForEvent("serviceworker", { timeout: 15000 }));
  const extId = new URL(sw.url()).host;
  const walletPage = await ctx.newPage(); await walletPage.goto("http://127.0.0.1:18081/");
  popup = await ctx.newPage();
  popup.on("pageerror", (e) => pageErrors.push(e.message));
  // Popup в браузере — «пузырь», активная вкладка = сайт. Эмулируем это: tabs.query возвращает вкладку с моком.
  await popup.addInitScript(() => {
    const q = chrome.tabs.query.bind(chrome.tabs);
    chrome.tabs.query = async () => q({ url: "http://127.0.0.1:18081/*" });
  });
  await popup.goto(`chrome-extension://${extId}/popup.html`);
  const txt = (loc) => loc.innerText();

  // 1. привязка
  await popup.getByPlaceholder("https://your-service.up.railway.app").fill(boot.url);
  await popup.getByPlaceholder("Code from /link in @lp_mi_bot").fill(boot.code);
  await popup.getByRole("button", { name: "Link", exact: true }).click();
  await popup.locator("#wallet-chip", { hasText: OWNER.slice(0, 6) }).waitFor({ timeout: 10000 });
  check("привязка по коду из /link, в шапке активный кошелёк", true);
  const again = await popup.evaluate(async ({ url, code }) => (await fetch(url + "/api/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) })).status, boot);
  check("тот же код второй раз не работает", again === 400, `HTTP ${again}`);

  // 2. позиции
  await popup.getByRole("button", { name: "Positions" }).click();
  await popup.locator("#view .card .pair", { hasText: "ETH/USDC" }).waitFor({ timeout: 15000 });
  const cards = await popup.locator("#view .card").count();
  check("на вкладке Positions 2 карточки", cards === 2, `cards=${cards}`);
  const t = await txt(popup.locator("#view"));
  check("бейджи in range / out of range", t.includes("in range") && t.includes("out of range"));
  const v3 = popup.locator(".card", { hasText: "#555" });
  check("у v3-позиции Close и Collect выключены", (await v3.getByRole("button", { name: "Close" }).isDisabled()) && (await v3.getByRole("button", { name: "Collect fees" }).isDisabled()));

  // 2b. единицы диапазона: USD (USDT-стейблы) и ETH
  const live_card = popup.locator(".card", { hasText: `#${live.id}` });
  await popup.locator("#view select").selectOption("usd");
  await popup.locator("#view .card .nums span", { hasText: "$1,500" }).first().waitFor({ timeout: 15000 });
  check("рендж в USD: границы со знаком $ ($1,500 … $4,500)", (await txt(live_card)).includes("$4,500"));
  await popup.locator("#view select").selectOption("eth");
  await popup.locator("#view .card .nums span", { hasText: "Ξ" }).first().waitFor({ timeout: 20000 });
  const ethTxt = await txt(live_card);
  const m = ethTxt.match(/([\d.]+) Ξ\s+now/);
  check("рендж в ETH: нижняя граница = 1500 / курс ETH (0.4–0.8 Ξ при курсе 1.9–3.7k)", !!m && Number(m[1]) > 0.4 && Number(m[1]) < 0.8, ethTxt.replace(/\n/g, " ").slice(0, 90));
  await popup.locator("#view select").selectOption("pool");

  // 3. журнал
  const card = popup.locator(".card", { hasText: `#${live.id}` });
  await card.getByRole("button", { name: "Journal" }).click();
  await card.getByPlaceholder("Note for this position").fill("вход после дампа");
  await card.getByPlaceholder("Result, $ (e.g. sold on GMGN)").fill("12.5");
  await card.getByRole("button", { name: "Save" }).click();
  await popup.locator("#status", { hasText: "Saved to journal" }).waitFor();
  await popup.getByRole("button", { name: "Journal", exact: true }).first().click();
  await popup.locator("table").waitFor();
  check("журнал: сумма по монете ETH/USDC = $12.5", (await txt(popup.locator("#view"))).includes("$12.5"));

  // 4. смена кошелька
  await popup.getByRole("button", { name: "Settings" }).click();
  await popup.locator(".card", { hasText: "second" }).click();
  await popup.locator("#wallet-chip", { hasText: "0xd4d4" }).waitFor({ timeout: 10000 });
  check("смена активного кошелька отражается в шапке", true);
  await popup.locator(".card", { hasText: "main" }).click();
  await popup.locator("#wallet-chip", { hasText: OWNER.slice(0, 6) }).waitFor({ timeout: 10000 });

  // 5. Collect и Close через кошелёк
  const decode = (data) => { const [unlock] = POSM_IFACE.decodeFunctionData("modifyLiquidities", data); const [a, p] = AbiCoder.defaultAbiCoder().decode(["bytes", "bytes[]"], unlock); return { actions: [...Buffer.from(a.slice(2), "hex")], params: p }; };
  const calls = () => walletPage.evaluate(() => window.__calls);
  const runAction = async (btn, doneText) => {
    await popup.getByRole("button", { name: "Positions" }).click();
    const c = popup.locator(".card", { hasText: `#${live.id}` }); await c.waitFor();
    await c.getByRole("button", { name: btn }).click();
    await popup.locator("#modal h3").waitFor({ timeout: 20000 });
    const modal = await txt(popup.locator("#modal"));
    await popup.locator("#modal").getByRole("button", { name: /via wallet/ }).click();
    await popup.locator("#status", { hasText: doneText }).waitFor({ timeout: 60000 });
    return modal;
  };
  await walletPage.evaluate(() => (window.__calls.length = 0));
  const m1 = await runAction("Collect fees", "fees collected");
  check("Collect: в модалке симуляция OK", /Simulation\s*OK/.test(m1.replace(/\n/g, " ")), m1.replace(/\n/g, " | ").slice(0, 120));
  let cs = await calls(); let send = cs.find((c) => c.method === "eth_sendTransaction");
  let d = decode(send.params[0].data);
  check("Collect: в кошелёк ушли requestAccounts → switchChain(0x2105) → sendTransaction", cs.map((c) => c.method).join(",") === "eth_requestAccounts,wallet_switchEthereumChain,eth_sendTransaction" && cs[1].params[0].chainId === "0x2105");
  check("Collect: tx на PositionManager, actions DECREASE+TAKE_PAIR, получатель = основной кошелёк",
    send.params[0].to.toLowerCase() === posm.toLowerCase() && d.actions.join() === "1,17" && AbiCoder.defaultAbiCoder().decode(["address", "address", "address"], d.params[1])[2].toLowerCase() === OWNER.toLowerCase());
  await walletPage.evaluate(() => (window.__calls.length = 0));
  await runAction("Close", "position closed");
  send = (await calls()).find((c) => c.method === "eth_sendTransaction"); d = decode(send.params[0].data);
  check("Close: actions BURN+TAKE_PAIR", d.actions.join() === "3,17");

  // 6. защита от чужого аккаунта в кошельке
  await walletPage.evaluate(() => { localStorage.acc = "0x" + "99".repeat(20); window.__calls.length = 0; });
  await popup.getByRole("button", { name: "Positions" }).click();
  const c2 = popup.locator(".card", { hasText: `#${live.id}` }); await c2.waitFor();
  await c2.getByRole("button", { name: "Collect fees" }).click();
  await popup.locator("#modal h3").waitFor({ timeout: 20000 });
  await popup.locator("#modal").getByRole("button", { name: /via wallet/ }).click();
  await popup.locator("#status .err").waitFor({ timeout: 20000 });
  check("неверный аккаунт в кошельке: ошибка и НИКАКОЙ отправки tx", (await txt(popup.locator("#status"))).includes("Переключите аккаунт") && !(await calls()).some((c) => c.method === "eth_sendTransaction"));
  await walletPage.evaluate(() => { delete localStorage.acc; });

  // 7. Grid: реальный пул ETH/USDC v4 на Base, превью
  const ZERO = "0x0000000000000000000000000000000000000000", USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
  const pid = poolIdOf({ currency0: ZERO, currency1: USDC, fee: 100, tickSpacing: 1, hooks: ZERO });
  await popup.getByRole("button", { name: "Grid" }).click();
  await popup.getByPlaceholder(/Pool ID/).fill(pid);
  await popup.getByRole("button", { name: "Find" }).click();
  await popup.locator("#view .pair", { hasText: "ETH / USDC" }).waitFor({ timeout: 30000 });
  check("Grid: пул найден по Pool ID и подтверждён on-chain (ETH/USDC)", true);
  await popup.locator("select").nth(1).selectOption("asks");             // side (0 — сеть)
  await popup.getByPlaceholder("0.0").fill("0.5");
  await popup.getByRole("button", { name: "Preview" }).click();
  await popup.locator("#view table tbody tr").first().waitFor({ timeout: 30000 });
  const rows = await popup.locator("#view table tbody tr").count();
  check("Grid: превью 5 диапазонов + строка итогов", rows === 6, `rows=${rows}`);
  await popup.getByPlaceholder("0.0").fill("1000000000");
  await popup.getByRole("button", { name: "Preview" }).click();
  await popup.locator("#view .warn.neg").waitFor({ timeout: 30000 });
  check("Grid: при нехватке средств есть предупреждение и Create Grid выключен", await popup.getByRole("button", { name: "Create Grid" }).isDisabled());

  // 8. Grid на реальном ПЕРЕВЁРНУТОМ пуле (нативный ETH = токен0): интерфейс говорит на языке актива, а не token0/token1
  await popup.locator("select").first().selectOption("robinhood");
  await popup.getByPlaceholder(/Pool ID/).fill("0x6d9af55c4cb0d740dc5237e8fe2d60e017412fb1af4e2d81e948124797e56748");
  await popup.getByRole("button", { name: "Find" }).click();
  await popup.locator("#view .pair", { hasText: "CRCL / ETH" }).waitFor({ timeout: 30000 });
  const head = await txt(popup.locator("#view .card").first());
  check("перевёрнутый пул: заголовок «CRCL / ETH» и пометка про обратную цену пула", head.includes("CRCL / ETH") && head.includes("inverse"), head.replace(/\n/g, " | ").slice(0, 120));
  const sideOpts = await popup.locator("select").nth(1).locator("option").allInnerTexts();
  check("стороны названы в терминах актива: Bids — buy CRCL, deposit ETH", sideOpts[0].includes("buy CRCL") && sideOpts[0].includes("deposit ETH"), sideOpts[0]);
  await popup.locator("select").nth(1).selectOption("bids");             // форма помнит прошлый выбор — задаём явно
  await popup.getByPlaceholder("0.0").fill("0.1");
  await popup.getByRole("button", { name: "Preview" }).click();
  await popup.locator("#view table tbody tr").first().waitFor({ timeout: 30000 });
  const cells = await popup.locator("#view table tbody tr").first().locator("td").allInnerTexts();
  const pcts = cells[2].split("…").map(Number);
  check("bids: обе границы первого диапазона НИЖЕ цены актива (отрицательные %) и в полосе 2…20%", pcts.every((x) => x < 0 && x > -21), `% from now = ${cells[2]}`);
  // asks на том же пуле: депозит — CRCL (токен1), диапазоны ВЫШЕ цены актива
  await popup.locator("select").nth(1).selectOption("asks");
  await popup.getByPlaceholder("0.0").fill("1000");
  await popup.getByRole("button", { name: "Preview" }).click();
  await popup.locator("#view table tbody tr").first().waitFor({ timeout: 30000 });
  const acells = await popup.locator("#view table tbody tr").first().locator("td").allInnerTexts();
  const apcts = acells[2].split("…").map(Number);
  check("asks на том же пуле: обе границы ВЫШЕ цены актива (положительные %)", apcts.every((x) => x > 0), `% from now = ${acells[2]}`);
  await popup.locator("select").nth(1).selectOption("bids");
  await popup.getByPlaceholder("0.0").fill("0.1");
  await popup.getByRole("button", { name: "Preview" }).click();
  await popup.locator("#view table thead th", { hasText: "CRCL price, ETH" }).waitFor({ timeout: 30000 });
  const thead = await txt(popup.locator("#view table thead"));
  check("в таблице цена актива в котировке: «CRCL price, ETH»", thead.includes("CRCL price, ETH"), thead.replace(/\n/g, " ").slice(0, 60));

  check("в консоли popup нет необработанных ошибок", pageErrors.length === 0, pageErrors.join("; "));
} catch (e) {
  check("сценарий завершился без исключения", false, e.message.split("\n")[0]);
  if (popup) console.log("--- popup сейчас ---\n" + (await popup.locator("body").innerText().catch(() => "?")).slice(0, 600) + "\nerrors: " + pageErrors.join("; "));
} finally {
  await ctx.close(); site.close(); srv.kill("SIGTERM");
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
