import { api, settings } from "../api.js";
import { h, status, short } from "./ui.js";

export async function render(view, ctx) {
  const cfg = await settings.load();
  const url = h("input", { value: cfg.apiUrl, placeholder: "https://your-service.up.railway.app" });
  const code = h("input", { placeholder: "Code from /link in @lp_mi_bot", autocapitalize: "characters" });
  const els = [
    h("label", { text: "API URL" }), h("div", { class: "row" }, url,
      h("button", { class: "btn fit secondary", text: "Save", onclick: async () => { await settings.save({ apiUrl: url.value.trim().replace(/\/+$/, "") }); status("Saved", "ok"); } })),
    h("label", { text: cfg.token ? "Linked ✓ — re-link with a new code:" : "Link to your Telegram account" }),
    h("div", { class: "row" }, code, h("button", { class: "btn fit", text: "Link", onclick: async () => {
      try {
        await settings.save({ apiUrl: url.value.trim().replace(/\/+$/, "") });
        const { token } = await api.link(code.value.trim());
        await settings.save({ token }); status("Linked", "ok"); await ctx.reload();
      } catch (e) { status(e.message, "err"); }
    } })),
    h("div", { class: "hint", text: "Send /link to the bot, enter the code here within 10 minutes." }),
  ];
  if (cfg.token && ctx.me) {
    const add = h("input", { placeholder: "0x… wallet address to track" });
    els.push(h("label", { text: "Wallets (tap to switch the active one)" }),
      ...ctx.me.wallets.map((w) => h("div", { class: "card", style: "cursor:pointer;padding:6px 10px;margin:6px 0", onclick: async () => {
        if (w.is_active) return;
        try { await api.activate(w.id); status(`Switched to ${short(w.address)}`, "ok"); await ctx.reload(); } catch (e) { status(e.message, "err"); }
      } }, h("span", { class: "pair", text: `${w.is_active ? "✅ " : ""}${w.label || short(w.address)}` }), " ", h("span", { class: "sub", text: w.address }))),
      h("div", { class: "row" }, add, h("button", { class: "btn fit secondary", text: "Add", onclick: async () => {
        try { await api.addWallet(add.value.trim()); await ctx.reload(); } catch (e) { status(e.message, "err"); }
      } })),
      h("div", { class: "hint", text: "Only the address is stored. Transactions are signed in Rabby — this extension never sees a private key." }),
      h("div", { class: "actions" }, h("button", { class: "btn secondary", text: "Disconnect", onclick: async () => { await settings.clearToken(); await ctx.reload(); } })));
  }
  view.replaceChildren(...els);
}
