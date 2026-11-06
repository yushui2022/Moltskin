/* 打开（或聚焦）Moltskin 控制台标签页。 */
async function resolveManagerUrl() {
  try {
    const res = await fetch(browser.runtime.getURL("config.json"));
    const data = await res.json();
    if (data && data.url) return data.url.replace(/\/$/, "");
  } catch (err) {
    /* 配置文件缺失时退回默认地址 */
  }
  return "http://127.0.0.1:8765";
}

async function openManager() {
  const url = await resolveManagerUrl();
  try {
    const tabs = await browser.tabs.query({ url: url + "/*" });
    if (tabs.length) {
      await browser.tabs.update(tabs[0].id, { active: true });
      if (tabs[0].windowId != null) {
        await browser.windows.update(tabs[0].windowId, { focused: true });
      }
      return;
    }
  } catch (err) {
    /* query 失败就直接新建 */
  }
  await browser.tabs.create({ url });
}
