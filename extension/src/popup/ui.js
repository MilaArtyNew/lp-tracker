// Мини-хелперы DOM. Данные из сети/API (символы токенов и т.п.) недоверенные: только textContent, без innerHTML.
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

const statusEl = () => document.getElementById("status");
export function status(text, kind = "") {
  statusEl().replaceChildren(text ? h("div", { class: `msg ${kind}`, text }) : "");
}
export const clearStatus = () => status("");

export const short = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "—");
export const usd = (n) => (n == null ? "—" : `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 })}`);
export function sig(n, digits = 5) {
  n = Number(n);
  if (!isFinite(n)) return "—";
  if (n === 0) return "0";
  const abs = Math.abs(n);
  if (abs >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return Number(n.toPrecision(digits)).toString().replace(/e-(\d+)/, (_, e) => `e-${e}`);
}

// Подтверждение перед подписью. Возвращает Promise<boolean>. Если disabledReason — подтвердить нельзя.
export function confirmModal({ title, rows = [], warnings = [], confirmText = "Confirm", danger = false, disabledReason = "" }) {
  const modal = document.getElementById("modal");
  return new Promise((resolve) => {
    const done = (v) => { modal.hidden = true; modal.replaceChildren(); resolve(v); };
    const ok = h("button", { class: `btn ${danger ? "danger" : ""}`, text: confirmText, disabled: !!disabledReason, onclick: () => done(true) });
    modal.replaceChildren(h("div", { class: "box" },
      h("h3", { text: title }),
      rows.map(([k, v, cls]) => h("div", { class: "kv" }, h("span", { text: k }), h("span", { class: cls || "", text: v }))),
      warnings.map((w) => h("div", { class: "warn", text: w })),
      disabledReason && h("div", { class: "warn neg", text: disabledReason }),
      h("div", { class: "actions" }, ok, h("button", { class: "btn secondary", text: "Cancel", onclick: () => done(false) })),
    ));
    modal.hidden = false;
  });
}
