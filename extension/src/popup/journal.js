import { api } from "../api.js";
import { h, usd } from "./ui.js";

export async function render(view) {
  const rows = await api.journal();
  const total = rows.reduce((s, r) => s + Number(r.manual_pnl_usd), 0);
  view.replaceChildren(
    rows.length ? h("table", {},
      h("thead", {}, h("tr", {}, ...["Coin", "Pos.", "Result", "Fees"].map((t) => h("th", { text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", { text: `${r.symbol0}/${r.symbol1}` }), h("td", { text: r.positions }),
        h("td", { class: Number(r.manual_pnl_usd) >= 0 ? "pos" : "neg", text: usd(r.manual_pnl_usd) }), h("td", { text: usd(r.fees_usd) }))),
        h("tr", {}, h("td", { text: "Total" }), h("td"), h("td", { class: total >= 0 ? "pos" : "neg", text: usd(total) }), h("td")))
    ) : h("div", { class: "empty", text: "Journal is empty. Add a note and the result on a position card." }),
    h("div", { class: "hint", text: "«Result» is the amount you enter by hand per position (e.g. after selling on GMGN); fees come from on-chain data. PnL/ROI from full deposit/withdraw history is not calculated yet." }));
}
