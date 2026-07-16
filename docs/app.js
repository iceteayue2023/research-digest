const listEl = document.getElementById("article-list");
const dateSelect = document.getElementById("date-select");
const countBadge = document.getElementById("count-badge");

function scoreClass(score) {
  if (score >= 8) return "";
  if (score >= 6) return "mid";
  return "mid";
}

function renderArticles(data) {
  countBadge.textContent = `${data.count} 篇`;

  if (!data.articles || data.articles.length === 0) {
    listEl.innerHTML = '<p class="empty">这天没有匹配到相关文章。</p>';
    return;
  }

  listEl.innerHTML = data.articles.map(a => `
    <article class="card">
      <div class="card-top">
        <span class="journal-badge">${escapeHtml(a.journal)}</span>
        <span class="score ${scoreClass(a.relevance_score)}">相关度 ${a.relevance_score}/10</span>
      </div>
      <p class="title-zh">${escapeHtml(a.title_zh || a.title)}</p>
      <p class="title-en">${escapeHtml(a.title)}</p>
      <p class="field"><span class="label">关键结论</span>${escapeHtml(a.key_conclusion || "暂无")}</p>
      <p class="field"><span class="label">作者单位</span>${escapeHtml(a.affiliation || "未提供，见原文")}</p>
      <p class="field"><span class="label">与你的关联</span>${escapeHtml(a.relevance_note || "")}</p>
      <a class="read-more" href="${a.link}" target="_blank" rel="noopener">查看原文 →</a>
    </article>
  `).join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

async function loadDate(dateStr) {
  listEl.innerHTML = '<p class="loading">加载中…</p>';
  try {
    const res = await fetch(`data/${dateStr}.json`, { cache: "no-store" });
    if (!res.ok) throw new Error("not found");
    renderArticles(await res.json());
  } catch (e) {
    listEl.innerHTML = '<p class="empty">这天还没有数据。</p>';
  }
}

async function init() {
  try {
    const [indexRes, latestRes] = await Promise.all([
      fetch("data/index.json", { cache: "no-store" }),
      fetch("data/latest.json", { cache: "no-store" }),
    ]);

    let dates = [];
    if (indexRes.ok) dates = await indexRes.json();

    if (dates.length === 0) {
      listEl.innerHTML = '<p class="empty">还没有生成过日报，等第一次定时任务跑完就会显示内容。</p>';
      return;
    }

    dateSelect.innerHTML = dates.map(d => `<option value="${d}">${d}</option>`).join("");

    if (latestRes.ok) {
      const latest = await latestRes.json();
      dateSelect.value = latest.date;
      renderArticles(latest);
    } else {
      dateSelect.value = dates[0];
      loadDate(dates[0]);
    }

    dateSelect.addEventListener("change", () => loadDate(dateSelect.value));
  } catch (e) {
    listEl.innerHTML = '<p class="empty">加载失败，请检查网络后下拉刷新。</p>';
  }
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("service-worker.js").catch(() => {});
  });
}

init();
