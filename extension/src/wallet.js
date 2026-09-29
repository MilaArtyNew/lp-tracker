// Подпись через кошелёк пользователя (Rabby/MetaMask). У страницы расширения нет window.ethereum,
// поэтому запросы выполняются в MAIN-мире активной вкладки (нужна любая обычная http(s)-страница).
// Приватных ключей расширение не видит и не хранит.
import { withRpc, CHAINS } from "./core/chain.js";

export class WalletError extends Error {}

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !/^https?:/.test(tab.url || "")) {
    throw new WalletError("Откройте любую обычную веб-страницу (например, gmgn.ai) во вкладке с Rabby и повторите.");
  }
  return tab.id;
}

async function request(method, params) {
  const tabId = await activeTabId();
  let out;
  try {
    [{ result: out }] = await chrome.scripting.executeScript({
      target: { tabId }, world: "MAIN", args: [method, params],
      func: async (method, params) => {
        try {
          if (!window.ethereum) return { error: "no_wallet" };
          return { result: await window.ethereum.request({ method, params }) };
        } catch (e) { return { error: (e && e.message) || String(e), code: e && e.code }; }
      },
    });
  } catch (e) { throw new WalletError(`Не удалось обратиться к вкладке: ${e.message}`); }
  if (!out) throw new WalletError("Вкладка не ответила — перезагрузите страницу.");
  if (out.error === "no_wallet") throw new WalletError("В этой вкладке нет кошелька (window.ethereum). Включите Rabby и перезагрузите страницу.");
  if (out.error) throw new WalletError(out.code === 4001 ? "Отклонено в кошельке." : out.error);
  return out.result;
}

const hex = (n) => "0x" + BigInt(n).toString(16);

// Гарантирует: кошелёк подключён, аккаунт = активному кошельку расширения, сеть переключена.
export async function ensureAccount(chain, expected) {
  const accounts = await request("eth_requestAccounts", []);
  const acc = (accounts[0] || "").toLowerCase();
  if (!expected || acc !== expected.toLowerCase()) {
    throw new WalletError(`В кошельке выбран ${acc.slice(0, 8)}…, а активный кошелёк расширения — ${(expected || "?").slice(0, 8)}…. Переключите аккаунт в Rabby.`);
  }
  const chainId = hex(CHAINS[chain].chainId);
  try { await request("wallet_switchEthereumChain", [{ chainId }]); }
  catch (e) { throw new WalletError(`Не удалось переключить сеть на ${CHAINS[chain].name}: ${e.message}`); }
  return acc;
}

export async function sendTx(chain, from, tx) {
  const params = { from, to: tx.to, data: tx.data };
  if (tx.value && tx.value > 0n) params.value = hex(tx.value);
  return request("eth_sendTransaction", [params]);
}

export async function waitReceipt(chain, hash, timeoutMs = 180000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const r = await withRpc(chain, (p) => p.getTransactionReceipt(hash)).catch(() => null);
    if (r) return r;
    await new Promise((r) => setTimeout(r, 2500));
  }
  throw new WalletError("Транзакция не подтвердилась за 3 минуты — проверьте в эксплорере.");
}
