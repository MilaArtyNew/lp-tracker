// Фон: раз в минуту берёт позиции из API и показывает на иконке число позиций вне диапазона.
import { api, settings } from "./api.js";

async function refreshBadge() {
  try {
    const { token } = await settings.load();
    if (!token) return chrome.action.setBadgeText({ text: "" });
    const { positions } = await api.positions("open");
    const out = positions.filter((p) => p.in_range === false).length;
    await chrome.action.setBadgeBackgroundColor({ color: out ? "#dc2626" : "#16a34a" });
    await chrome.action.setBadgeText({ text: out ? String(out) : "" });
  } catch { /* нет связи — оставляем прошлое значение */ }
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("badge", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "badge") refreshBadge(); });
chrome.runtime.onStartup.addListener(refreshBadge);
