const listEl = document.getElementById("article-list");
const dateSelect = document.getElementById("date-select");
const countBadge = document.getElementById("count-badge");
const dateRow = document.getElementById("date-row");
const folderRow = document.getElementById("folder-row");
const searchInput = document.getElementById("search-input");
const tabButtons = document.querySelectorAll(".tab");

const FAVORITES_KEY = "rd_favorites";
const NOTES_KEY = "rd_notes";
const DEFAULT_FOLDER = "默认";

let currentTab = "daily";
let currentDailyData = null;
let currentFolder = "全部";
let searchTerm = "";
let archiveCache = null; // 懒加载：跨所有历史日期的文章缓存，仅在搜索时用到

// ---------- localStorage helpers ----------

function readStore(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}");
  } catch (e) {
    return {};
  }
}

function writeStore(key, obj) {
  localStorage.setItem(key, JSON.stringify(obj));
}

function articleSnapshot(a) {
  return {
    id: a.id,
    title: a.title,
    title_zh: a.title_zh,
    journal: a.journal,
    link: a.link,
    key_conclusion: a.key_conclusion,
    relevance_note: a.relevance_note,
    affiliation: a.affiliation,
    relevance_score: a.relevance_score,
    scientific_question: a.scientific_question,
    contributions_limitations: a.contributions_limitations,
    follow_up_research: a.follow_up_research,
    next_step_perspective: a.next_step_perspective,
    why_this_journal: a.why_this_journal,
    author_profile: a.author_profile,
    related_papers: a.related_papers,
    image_url: a.image_url,
  };
}

function isFavorited(id) {
  return !!readStore(FAVORITES_KEY)[id];
}

function toggleFavorite(article) {
  const favs = readStore(FAVORITES_KEY);
  if (favs[article.id]) {
    delete favs[article.id];
  } else {
    favs[article.id] = {
      savedAt: new Date().toISOString(),
      folder: DEFAULT_FOLDER,
      article: articleSnapshot(article),
    };
  }
  writeStore(FAVORITES_KEY, favs);
  return !!favs[article.id];
}

function getFolders() {
  const favs = readStore(FAVORITES_KEY);
  const folders = new Set([DEFAULT_FOLDER]);
  Object.values(favs).forEach((e) => folders.add(e.folder || DEFAULT_FOLDER));
  return Array.from(folders);
}

function moveFavoriteToFolder(id, folder) {
  const favs = readStore(FAVORITES_KEY);
  if (favs[id]) {
    favs[id].folder = folder;
    writeStore(FAVORITES_KEY, favs);
  }
}

function getNoteText(id) {
  const notes = readStore(NOTES_KEY);
  return notes[id]?.text || "";
}

function saveNote(article, text) {
  const notes = readStore(NOTES_KEY);
  if (!text.trim()) {
    delete notes[article.id];
  } else {
    notes[article.id] = {
      text,
      updatedAt: new Date().toISOString(),
      article: articleSnapshot(article),
    };
  }
  writeStore(NOTES_KEY, notes);
}

// ---------- rendering helpers ----------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function scoreClass(score) {
  return score >= 8 ? "" : "mid";
}

function matchesSearch(a, term) {
  if (!term) return true;
  const haystack = [a.title, a.title_zh, a.key_conclusion, a.relevance_note, a.journal]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(term.toLowerCase());
}

function hasDeepDive(a) {
  return !!(a.scientific_question || a.contributions_limitations || a.follow_up_research);
}

function cardHtml(a, { showDate = false, folderControl = null } = {}) {
  const favActive = isFavorited(a.id);
  const noteText = getNoteText(a.id);
  return `
    <article class="card" data-id="${escapeHtml(a.id)}">
      ${a.image_url ? `<img class="article-img" src="${escapeHtml(a.image_url)}" alt="" loading="lazy" onerror="this.remove()">` : ""}
      <div class="card-top">
        <span class="journal-badge">${escapeHtml(a.journal)}</span>
        <div class="card-top-right">
          ${showDate ? `<span class="date-tag">${escapeHtml(a.savedDate || "")}</span>` : ""}
          <span class="score ${scoreClass(a.relevance_score)}">相关度 ${a.relevance_score}/10</span>
        </div>
      </div>
      <p class="title-zh">${escapeHtml(a.title_zh || a.title)}</p>
      <p class="title-en">${escapeHtml(a.title)}</p>
      <p class="field"><span class="label">关键结论</span>${escapeHtml(a.key_conclusion || "暂无")}</p>
      <p class="field"><span class="label">作者单位</span>${escapeHtml(a.affiliation || "未提供，见原文")}</p>
      <p class="field"><span class="label">与你的关联</span>${escapeHtml(a.relevance_note || "")}</p>

      <div class="card-actions">
        <a class="read-more" href="${a.link}" target="_blank" rel="noopener">查看原文 →</a>
        ${hasDeepDive(a) ? `<button class="deep-toggle-btn" data-id="${escapeHtml(a.id)}">🔍 深度解读</button>` : ""}
        <button class="fav-btn ${favActive ? "active" : ""}" data-id="${escapeHtml(a.id)}">
          ${favActive ? "★ 已收藏" : "☆ 收藏"}
        </button>
        <button class="note-toggle-btn" data-id="${escapeHtml(a.id)}">📝 笔记${noteText ? " ●" : ""}</button>
        ${folderControl !== null ? `
        <select class="folder-select" data-id="${escapeHtml(a.id)}">
          ${getFolders().map(f => `<option value="${escapeHtml(f)}" ${f === folderControl ? "selected" : ""}>${escapeHtml(f)}</option>`).join("")}
          <option value="__new__">＋ 新建文件夹…</option>
        </select>` : ""}
      </div>

      ${hasDeepDive(a) ? `
      <div class="deep-box" data-id="${escapeHtml(a.id)}" hidden>
        <p class="field"><span class="label">科学问题</span>${escapeHtml(a.scientific_question || "")}</p>
        <p class="field"><span class="label">贡献与局限</span>${escapeHtml(a.contributions_limitations || "")}</p>
        <p class="field"><span class="label">后续研究方向</span>${escapeHtml(a.follow_up_research || "")}</p>
        <p class="field"><span class="label">对你研究的启发</span>${escapeHtml(a.next_step_perspective || "")}</p>
        <p class="field"><span class="label">为何能发这个期刊</span>${escapeHtml(a.why_this_journal || "")}</p>
        ${a.author_profile ? `
        <div class="sub-block">
          <p class="sub-title">作者简介 · ${escapeHtml(a.author_profile.name)}</p>
          <p class="field-plain">${escapeHtml(a.author_profile.intro || "")}</p>
          ${a.author_profile.other_works && a.author_profile.other_works.length ? `
          <p class="sub-title">该作者的其他相关研究</p>
          <ul class="ref-list">
            ${a.author_profile.other_works.map(w => `
              <li>${w.doi ? `<a href="https://doi.org/${escapeHtml(w.doi)}" target="_blank" rel="noopener">${escapeHtml(w.title)}</a>` : escapeHtml(w.title)}
                ${w.journal ? ` <span class="ref-meta">· ${escapeHtml(w.journal)}</span>` : ""}
                ${w.year ? ` <span class="ref-meta">${escapeHtml(String(w.year))}</span>` : ""}
              </li>
            `).join("")}
          </ul>` : ""}
        </div>` : ""}
        ${a.related_papers && a.related_papers.length ? `
        <div class="sub-block">
          <p class="sub-title">更多相关文献</p>
          <ul class="ref-list">
            ${a.related_papers.map(r => `
              <li>${r.link ? `<a href="${r.link}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>` : escapeHtml(r.title)}
                ${r.venue ? ` <span class="ref-meta">· ${escapeHtml(r.venue)}</span>` : ""}
                ${r.year ? ` <span class="ref-meta">${escapeHtml(String(r.year))}</span>` : ""}
              </li>
            `).join("")}
          </ul>
        </div>` : ""}
      </div>` : ""}

      <div class="note-box" data-id="${escapeHtml(a.id)}" hidden>
        <textarea class="note-input" placeholder="写点笔记…自动保存" data-id="${escapeHtml(a.id)}">${escapeHtml(noteText)}</textarea>
        <span class="note-status" data-id="${escapeHtml(a.id)}"></span>
      </div>
    </article>
  `;
}

function bindCardEvents(container, articleLookup, { onFolderChange = null } = {}) {
  container.querySelectorAll(".fav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const article = articleLookup[id];
      if (!article) return;
      const nowActive = toggleFavorite(article);
      btn.classList.toggle("active", nowActive);
      btn.textContent = nowActive ? "★ 已收藏" : "☆ 收藏";
      if (currentTab === "favorites") renderFavoritesView();
    });
  });

  container.querySelectorAll(".note-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const box = container.querySelector(`.note-box[data-id="${CSS.escape(id)}"]`);
      if (box) box.hidden = !box.hidden;
    });
  });

  container.querySelectorAll(".deep-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const box = container.querySelector(`.deep-box[data-id="${CSS.escape(id)}"]`);
      if (box) box.hidden = !box.hidden;
    });
  });

  container.querySelectorAll(".folder-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const id = sel.dataset.id;
      let folder = sel.value;
      if (folder === "__new__") {
        folder = (prompt("新建文件夹名称：") || "").trim();
        if (!folder) { sel.value = DEFAULT_FOLDER; return; }
      }
      moveFavoriteToFolder(id, folder);
      if (onFolderChange) onFolderChange();
    });
  });

  let debounceTimer;
  container.querySelectorAll(".note-input").forEach((textarea) => {
    textarea.addEventListener("input", () => {
      const id = textarea.dataset.id;
      const article = articleLookup[id];
      const statusEl = container.querySelector(`.note-status[data-id="${CSS.escape(id)}"]`);
      if (statusEl) statusEl.textContent = "保存中…";
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        saveNote(article, textarea.value);
        if (statusEl) statusEl.textContent = "已保存";
        const toggleBtn = container.querySelector(`.note-toggle-btn[data-id="${CSS.escape(id)}"]`);
        if (toggleBtn) toggleBtn.textContent = `📝 笔记${textarea.value.trim() ? " ●" : ""}`;
      }, 500);
    });
  });
}

// ---------- daily tab ----------

function renderArticles(data) {
  currentDailyData = data;
  const articles = (data.articles || []).filter((a) => matchesSearch(a, searchTerm));
  countBadge.textContent = `${articles.length} 篇`;

  if (articles.length === 0) {
    listEl.innerHTML = `<p class="empty">${searchTerm ? "没有匹配的文章。" : "这天没有匹配到相关文章。"}</p>`;
    return;
  }

  listEl.innerHTML = articles.map((a) => cardHtml(a)).join("");
  const lookup = Object.fromEntries(articles.map((a) => [a.id, a]));
  bindCardEvents(listEl, lookup);
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

async function loadArchiveCache() {
  if (archiveCache) return archiveCache;
  const indexRes = await fetch("data/index.json", { cache: "no-store" });
  const dates = indexRes.ok ? await indexRes.json() : [];
  const all = [];
  for (const d of dates) {
    try {
      const res = await fetch(`data/${d}.json`, { cache: "no-store" });
      if (!res.ok) continue;
      const day = await res.json();
      (day.articles || []).forEach((a) => all.push({ ...a, savedDate: d }));
    } catch (e) { /* 跳过读取失败的日期 */ }
  }
  archiveCache = all;
  return all;
}

async function renderArchiveSearch() {
  listEl.innerHTML = '<p class="loading">正在搜索历史日报…</p>';
  const all = await loadArchiveCache();
  const matched = all.filter((a) => matchesSearch(a, searchTerm));
  countBadge.textContent = `${matched.length} 篇（全部历史）`;

  if (matched.length === 0) {
    listEl.innerHTML = '<p class="empty">没有匹配的文章。</p>';
    return;
  }
  listEl.innerHTML = matched.map((a) => cardHtml(a, { showDate: true })).join("");
  const lookup = Object.fromEntries(matched.map((a) => [a.id, a]));
  bindCardEvents(listEl, lookup);
}

async function initDailyTab() {
  dateRow.style.display = searchTerm ? "none" : "flex";
  if (searchTerm) {
    renderArchiveSearch();
    return;
  }
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

    dateSelect.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");

    if (latestRes.ok) {
      const latest = await latestRes.json();
      dateSelect.value = latest.date;
      renderArticles(latest);
    } else {
      dateSelect.value = dates[0];
      loadDate(dates[0]);
    }
  } catch (e) {
    listEl.innerHTML = '<p class="empty">加载失败，请检查网络后下拉刷新。</p>';
  }
}

// ---------- favorites / notes tabs ----------

function renderFolderRow() {
  const folders = ["全部", ...getFolders()];
  folderRow.hidden = false;
  folderRow.innerHTML = folders.map((f) => `
    <button class="folder-chip ${f === currentFolder ? "active" : ""}" data-folder="${escapeHtml(f)}">${escapeHtml(f)}</button>
  `).join("");
  folderRow.querySelectorAll(".folder-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      currentFolder = chip.dataset.folder;
      renderFavoritesView();
    });
  });
}

function renderFavoritesView() {
  renderFolderRow();
  const favs = readStore(FAVORITES_KEY);
  let entries = Object.entries(favs).map(([id, e]) => ({ id, ...e }));
  if (currentFolder !== "全部") {
    entries = entries.filter((e) => (e.folder || DEFAULT_FOLDER) === currentFolder);
  }
  entries.sort((a, b) => (a.savedAt < b.savedAt ? 1 : -1));

  const articles = entries
    .map((e) => ({ ...e.article, savedDate: e.savedAt.slice(0, 10), _folder: e.folder || DEFAULT_FOLDER }))
    .filter((a) => matchesSearch(a, searchTerm));

  countBadge.textContent = `${articles.length} 篇`;

  if (articles.length === 0) {
    listEl.innerHTML = '<p class="empty">这个文件夹还没有收藏文章。</p>';
    return;
  }

  listEl.innerHTML = articles.map((a) => cardHtml(a, { showDate: true, folderControl: a._folder })).join("");
  const lookup = Object.fromEntries(articles.map((a) => [a.id, a]));
  bindCardEvents(listEl, lookup, { onFolderChange: renderFavoritesView });
}

function renderNotesView() {
  folderRow.hidden = true;
  const notes = readStore(NOTES_KEY);
  const entries = Object.values(notes).sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
  const articles = entries
    .map((e) => ({ ...e.article, savedDate: e.updatedAt.slice(0, 10) }))
    .filter((a) => matchesSearch(a, searchTerm));

  countBadge.textContent = `${articles.length} 篇`;

  if (articles.length === 0) {
    listEl.innerHTML = '<p class="empty">还没有写过笔记，在文章卡片上点"📝 笔记"试试。</p>';
    return;
  }

  listEl.innerHTML = articles.map((a) => cardHtml(a, { showDate: true })).join("");
  const lookup = Object.fromEntries(articles.map((a) => [a.id, a]));
  bindCardEvents(listEl, lookup);
  listEl.querySelectorAll(".note-box").forEach((box) => (box.hidden = false));
}

// ---------- social (Bluesky) tab ----------

let socialCache = null;

function socialCardHtml(p) {
  const time = p.created_at ? new Date(p.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
  return `
    <article class="social-card">
      <div class="social-top">
        <span class="social-handle">${escapeHtml(p.author_name)} · @${escapeHtml(p.handle)}</span>
        <span class="social-time">${escapeHtml(time)}</span>
      </div>
      <p class="social-text">${escapeHtml(p.text)}</p>
      ${p.external_thumb ? `<img class="article-img" src="${escapeHtml(p.external_thumb)}" alt="" loading="lazy" onerror="this.remove()">` : ""}
      ${p.external_uri ? `<a class="social-link" href="${escapeHtml(p.external_uri)}" target="_blank" rel="noopener">${escapeHtml(p.external_title || "查看链接")} →</a><br>` : ""}
      <a class="social-link" href="${escapeHtml(p.post_link)}" target="_blank" rel="noopener">在 Bluesky 上查看</a>
    </article>
  `;
}

async function renderSocialView() {
  folderRow.hidden = true;
  dateRow.style.display = "none";
  listEl.innerHTML = '<p class="loading">加载中…</p>';
  try {
    if (!socialCache) {
      const res = await fetch("data/social_latest.json", { cache: "no-store" });
      socialCache = res.ok ? await res.json() : { posts: [] };
    }
    const term = searchTerm.toLowerCase();
    const posts = (socialCache.posts || []).filter((p) =>
      !term || `${p.text} ${p.external_title || ""} ${p.external_description || ""}`.toLowerCase().includes(term)
    );
    countBadge.textContent = `${posts.length} 条`;
    if (posts.length === 0) {
      listEl.innerHTML = '<p class="empty">还没有匹配的动态。</p>';
      return;
    }
    listEl.innerHTML = posts.map(socialCardHtml).join("");
  } catch (e) {
    listEl.innerHTML = '<p class="empty">加载失败，请检查网络。</p>';
  }
}

// ---------- tabs ----------

function switchTab(tab) {
  currentTab = tab;
  tabButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
  folderRow.hidden = tab !== "favorites";

  if (tab === "daily") {
    dateRow.style.display = searchTerm ? "none" : "flex";
    if (searchTerm) renderArchiveSearch();
    else if (currentDailyData) renderArticles(currentDailyData);
    else initDailyTab();
  } else if (tab === "favorites") {
    dateRow.style.display = "none";
    renderFavoritesView();
  } else if (tab === "notes") {
    dateRow.style.display = "none";
    renderNotesView();
  } else if (tab === "social") {
    renderSocialView();
  }
}

tabButtons.forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));
dateSelect.addEventListener("change", () => loadDate(dateSelect.value));

let searchDebounce;
searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    searchTerm = searchInput.value.trim();
    switchTab(currentTab);
  }, 350);
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("service-worker.js").catch(() => {});
  });
}

initDailyTab();
