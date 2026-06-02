const state = {
  authenticated: false,
  repositories: [],
  repoNextLast: null,
  selectedRepo: null,
  tagCountByRepo: new Map(),
  loadingTagCountRepos: new Set(),
  tagCountGeneration: 0,
  tags: [],
  tagNextLast: null,
  metadataByTag: new Map(),
  loadingMetadataTags: new Set(),
  metadataGeneration: 0,
  selectedTags: new Set(),
  batchDeleting: false,
  repositoryPrefix: "",
  repoStorageByRepo: new Map(),
  repoStorageLoading: false,
  repoStorageGeneration: 0,
};

const els = {
  loginView: document.querySelector("#loginView"),
  dashboardView: document.querySelector("#dashboardView"),
  loginForm: document.querySelector("#loginForm"),
  registryInput: document.querySelector("#registryInput"),
  usernameInput: document.querySelector("#usernameInput"),
  passwordInput: document.querySelector("#passwordInput"),
  repositoryPrefixInput: document.querySelector("#repositoryPrefixInput"),
  insecureInput: document.querySelector("#insecureInput"),
  loginButton: document.querySelector("#loginButton"),
  loginMessage: document.querySelector("#loginMessage"),
  currentRegistry: document.querySelector("#currentRegistry"),
  currentUser: document.querySelector("#currentUser"),
  currentScope: document.querySelector("#currentScope"),
  scopeForm: document.querySelector("#scopeForm"),
  scopeInput: document.querySelector("#scopeInput"),
  applyScopeButton: document.querySelector("#applyScopeButton"),
  clearScopeButton: document.querySelector("#clearScopeButton"),
  logoutButton: document.querySelector("#logoutButton"),
  refreshCatalogButton: document.querySelector("#refreshCatalogButton"),
  globalNotice: document.querySelector("#globalNotice"),
  repoStorageText: document.querySelector("#repoStorageText"),
  repoStorageMeta: document.querySelector("#repoStorageMeta"),
  refreshRepoStorageButton: document.querySelector("#refreshRepoStorageButton"),
  repoSearch: document.querySelector("#repoSearch"),
  directRepoForm: document.querySelector("#directRepoForm"),
  directRepoInput: document.querySelector("#directRepoInput"),
  repoList: document.querySelector("#repoList"),
  repoCount: document.querySelector("#repoCount"),
  loadMoreReposButton: document.querySelector("#loadMoreReposButton"),
  selectedRepoTitle: document.querySelector("#selectedRepoTitle"),
  tagSummary: document.querySelector("#tagSummary"),
  refreshTagsButton: document.querySelector("#refreshTagsButton"),
  batchDeleteButton: document.querySelector("#batchDeleteButton"),
  selectAllTagsInput: document.querySelector("#selectAllTagsInput"),
  emptyState: document.querySelector("#emptyState"),
  tagTableWrap: document.querySelector("#tagTableWrap"),
  tagTableBody: document.querySelector("#tagTableBody"),
  loadMoreTagsButton: document.querySelector("#loadMoreTagsButton"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail ? `：${payload.detail}` : "";
    throw new Error(`${payload.message || "请求失败"}${detail}`);
  }
  return payload;
}

function setBusy(button, busy, text) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = text || "处理中";
    button.disabled = true;
    return;
  }
  button.textContent = button.dataset.label || button.textContent;
  button.disabled = false;
}

function showNotice(message, type = "info") {
  els.globalNotice.hidden = !message;
  els.globalNotice.textContent = message || "";
  els.globalNotice.classList.toggle("is-loading", type === "loading");
  els.globalNotice.classList.toggle("is-error", type === "error");
  els.globalNotice.classList.toggle("is-success", type === "success");
}

function setRepoListLoading(message) {
  els.repoList.innerHTML = `<div class="loading-state"><span></span><strong>${message}</strong></div>`;
}

function setTagListLoading(message) {
  els.emptyState.hidden = true;
  els.tagTableWrap.hidden = false;
  els.tagTableBody.innerHTML = `<tr><td colspan="6"><div class="loading-state inline"><span></span><strong>${message}</strong></div></td></tr>`;
}

function switchView(authenticated) {
  state.authenticated = authenticated;
  els.loginView.hidden = authenticated;
  els.dashboardView.hidden = !authenticated;
}

function normalizeRepositoryPrefix(value) {
  return (value || "").trim().replace(/^\/+|\/+$/g, "").replace(/\/+/g, "/");
}

function qualifyRepository(value) {
  const repo = normalizeRepositoryPrefix(value);
  const prefix = state.repositoryPrefix;
  if (!repo || !prefix) return repo;
  if (repo === prefix || repo.startsWith(`${prefix}/`)) return repo;
  if (!repo.includes("/")) return `${prefix}/${repo}`;
  return repo;
}

function updateDirectRepoPlaceholder() {
  const prefix = state.repositoryPrefix;
  els.directRepoInput.placeholder = prefix ? `输入镜像名，如 app（实际为 ${prefix}/app）` : "手动输入镜像名，如 library/alpine";
}

function updateConnection(connection) {
  state.repositoryPrefix = normalizeRepositoryPrefix(connection?.repositoryPrefix || "");
  els.currentRegistry.textContent = connection?.registry || "-";
  els.currentUser.textContent = connection?.username ? `用户：${connection.username}` : "匿名或 token 认证";
  els.currentScope.textContent = state.repositoryPrefix ? `范围：${state.repositoryPrefix}` : "范围：全部镜像";
  els.scopeInput.value = state.repositoryPrefix;
  updateDirectRepoPlaceholder();
  renderStorageSummary();
}

function filteredRepositories() {
  const query = els.repoSearch.value.trim().toLowerCase();
  if (!query) return state.repositories;
  return state.repositories.filter((name) => name.toLowerCase().includes(query));
}

function renderRepositories() {
  const repositories = filteredRepositories();
  els.repoCount.textContent = state.repositoryPrefix
    ? `${state.repositories.length} repositories under ${state.repositoryPrefix}`
    : `${state.repositories.length} repositories`;
  els.repoList.innerHTML = "";

  if (!repositories.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<strong>没有镜像</strong><span>当前过滤条件下没有可显示的仓库。</span>";
    els.repoList.append(empty);
  } else {
    repositories.forEach((repo) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `repo-item${repo === state.selectedRepo ? " is-active" : ""}`;
      button.innerHTML = `<span class="repo-count-badge"></span><span class="repo-name"></span><span class="repo-marker"></span>`;
      renderRepoCount(button.querySelector(".repo-count-badge"), repo);
      button.querySelector(".repo-name").textContent = repo;
      button.addEventListener("click", () => selectRepository(repo));
      els.repoList.append(button);
    });
    loadTagCountsForRepositories(repositories);
  }

  els.loadMoreReposButton.hidden = !state.repoNextLast;
}

function renderRepoCount(badge, repo) {
  const countInfo = state.tagCountByRepo.get(repo);
  badge.classList.toggle("is-loading", !countInfo && state.loadingTagCountRepos.has(repo));
  badge.classList.toggle("is-error", Boolean(countInfo?.error));
  badge.textContent = countInfo?.countText || (countInfo?.error ? "?" : "...");
  badge.title = countInfo?.error
    ? "tag 数读取失败"
    : countInfo
      ? `${countInfo.countText} tags`
      : "正在读取 tag 数";
}

function loadTagCountsForRepositories(repositories) {
  const candidates = repositories
    .filter((repo) => !state.tagCountByRepo.has(repo) && !state.loadingTagCountRepos.has(repo))
    .slice(0, 120);
  if (!candidates.length) return;

  const generation = state.tagCountGeneration;
  candidates.forEach((repo) => state.loadingTagCountRepos.add(repo));

  const workers = Array.from({ length: Math.min(4, candidates.length) }, async () => {
    while (candidates.length && generation === state.tagCountGeneration) {
      const repo = candidates.shift();
      try {
        const payload = await api(`/api/repositories/${encodeURIComponent(repo)}/tags/count?maxPages=20`);
        if (generation !== state.tagCountGeneration) return;
        state.tagCountByRepo.set(repo, payload);
      } catch (error) {
        if (generation !== state.tagCountGeneration) return;
        state.tagCountByRepo.set(repo, { error: true, countText: "?" });
      } finally {
        state.loadingTagCountRepos.delete(repo);
        if (generation === state.tagCountGeneration) renderRepositories();
      }
    }
  });

  Promise.all(workers).catch(() => null);
}

function renderTags() {
  const repo = state.selectedRepo;
  els.selectedRepoTitle.textContent = repo || "选择一个镜像";
  const selectedCount = currentSelectedTags().length;
  const loadingCount = state.loadingMetadataTags.size;
  els.tagSummary.textContent = repo
    ? `${state.tags.length} tags${selectedCount ? `，已选 ${selectedCount}` : ""}${loadingCount ? `，详情查询中 ${loadingCount}` : ""}`
    : "查看 tag 与 manifest digest";
  els.refreshTagsButton.disabled = !repo;
  els.emptyState.hidden = Boolean(repo);
  els.tagTableWrap.hidden = !repo;
  els.tagTableBody.innerHTML = "";

  updateTagSelectionControls();
  if (!repo) return;

  if (!state.tags.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6" class="digest-cell">该镜像没有可显示的 tag。</td>';
    els.tagTableBody.append(row);
  } else {
    sortedTags().forEach((tag) => {
      const metadata = state.metadataByTag.get(tag);
      const isLoading = state.loadingMetadataTags.has(tag);
      const isSelected = state.selectedTags.has(tag);
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="select-cell"><input type="checkbox" data-action="select" aria-label="选择 ${escapeHtml(tag)}" /></td>
        <td></td>
        <td class="size-cell"></td>
        <td class="digest-cell"></td>
        <td class="time-cell"></td>
        <td>
          <div class="tag-actions">
            <button class="mini-btn" type="button" data-action="metadata">详情</button>
            <button class="mini-btn danger" type="button" data-action="delete">删除</button>
          </div>
        </td>
      `;
      row.querySelector('[data-action="select"]').checked = isSelected;
      row.children[1].textContent = tag;
      row.children[2].textContent = formatSizeCell(metadata, isLoading);
      row.children[3].textContent = formatDigestCell(metadata, isLoading);
      row.children[4].textContent = formatTimeCell(metadata, isLoading);
      const selectInput = row.querySelector('[data-action="select"]');
      const metadataButton = row.querySelector('[data-action="metadata"]');
      const deleteButton = row.querySelector('[data-action="delete"]');
      metadataButton.disabled = isLoading || state.batchDeleting;
      metadataButton.textContent = isLoading ? "查询中" : "详情";
      deleteButton.disabled = state.batchDeleting;
      selectInput.disabled = state.batchDeleting;
      selectInput.addEventListener("change", (event) => toggleTagSelection(tag, event.currentTarget.checked));
      metadataButton.addEventListener("click", (event) => loadMetadata(tag, event.currentTarget));
      deleteButton.addEventListener("click", (event) => deleteTag(tag, event.currentTarget));
      els.tagTableBody.append(row);
    });
  }

  els.loadMoreTagsButton.hidden = !state.tagNextLast;
  renderStorageSummary();
}

function renderStorageSummary() {
  const repoUsage = state.selectedRepo ? state.repoStorageByRepo.get(state.selectedRepo) : null;
  els.repoStorageText.textContent = state.repoStorageLoading
    ? "计算中"
    : repoUsage
      ? formatStorageText(repoUsage)
      : state.selectedRepo
        ? "未计算"
        : "请选择镜像";
  els.repoStorageMeta.textContent = repoUsage
    ? formatStorageMeta(repoUsage)
    : state.selectedRepo
      ? "当前镜像下唯一 blob 估算占用"
      : "从左侧选择一个镜像";
  els.refreshRepoStorageButton.disabled = !state.selectedRepo || state.repoStorageLoading;
  els.refreshRepoStorageButton.textContent = state.repoStorageLoading ? "计算中" : "计算当前镜像";
}

function currentSelectedTags() {
  return state.tags.filter((tag) => state.selectedTags.has(tag));
}

function sortedTags() {
  const originalIndex = new Map(state.tags.map((tag, index) => [tag, index]));
  return [...state.tags].sort((left, right) => {
    const rightTime = metadataTimestamp(state.metadataByTag.get(right));
    const leftTime = metadataTimestamp(state.metadataByTag.get(left));
    if (rightTime !== leftTime) return rightTime - leftTime;
    return (originalIndex.get(left) || 0) - (originalIndex.get(right) || 0);
  });
}

function metadataTimestamp(metadata) {
  if (!metadata || metadata.error) return 0;
  const value = metadata.lastModified || metadata.createdAt || "";
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function updateTagSelectionControls() {
  const selectedCount = currentSelectedTags().length;
  els.batchDeleteButton.disabled = state.batchDeleting || !state.selectedRepo || selectedCount === 0;
  els.selectAllTagsInput.disabled = state.batchDeleting || !state.selectedRepo || state.tags.length === 0;
  els.selectAllTagsInput.checked = state.tags.length > 0 && selectedCount === state.tags.length;
  els.selectAllTagsInput.indeterminate = selectedCount > 0 && selectedCount < state.tags.length;
}

function toggleTagSelection(tag, checked) {
  if (checked) {
    state.selectedTags.add(tag);
  } else {
    state.selectedTags.delete(tag);
  }
  renderTags();
}

function toggleAllTagSelection(checked) {
  state.selectedTags.clear();
  if (checked) {
    state.tags.forEach((tag) => state.selectedTags.add(tag));
  }
  renderTags();
}

function formatTimeCell(metadata, isLoading) {
  if (isLoading) return "查询中";
  if (!metadata) return "未查询";
  if (metadata.error) return "读取失败";
  if (metadata.lastModified) return `更新 ${formatDateTime(metadata.lastModified)}`;
  if (metadata.createdAt) return `创建 ${formatDateTime(metadata.createdAt)}`;
  return metadata.timeNote || "未返回";
}

function formatSizeCell(metadata, isLoading) {
  if (isLoading) return "查询中";
  if (!metadata) return "未查询";
  if (metadata.error) return "读取失败";
  return metadata.imageSizeText || "未返回";
}

function formatDigestCell(metadata, isLoading) {
  if (isLoading) return "查询中";
  if (!metadata) return "未查询";
  if (metadata.error) return "读取失败";
  return metadata.digest || "未返回";
}

function formatStorageText(usage) {
  if (!usage) return "未计算";
  if (usage.totalText) return usage.totalText;
  return Number(usage.totalBytes) === 0 ? "0.00 MB" : "未返回";
}

function formatStorageMeta(usage) {
  const parts = [
    `${usage.repositoriesScanned || 0} 镜像`,
    `${usage.tagsScanned || 0} tags`,
    `${usage.uniqueBlobCount || 0} blobs`,
  ];
  if (usage.truncated) parts.push("结果已截断");
  if (usage.unknownTags) parts.push(`未知 ${usage.unknownTags}`);
  if (usage.manifestErrorCount) parts.push(`失败 ${usage.manifestErrorCount}`);
  return parts.join(" · ");
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function login(event) {
  event.preventDefault();
  els.loginMessage.textContent = "";
  setBusy(els.loginButton, true, "连接中");
  try {
    const payload = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        registry: els.registryInput.value,
        username: els.usernameInput.value,
        password: els.passwordInput.value,
        repositoryPrefix: els.repositoryPrefixInput.value,
        insecure: els.insecureInput.checked,
      }),
    });
    updateConnection(payload.connection);
    switchView(true);
    await loadCatalog(true);
  } catch (error) {
    els.loginMessage.textContent = error.message;
  } finally {
    setBusy(els.loginButton, false);
  }
}

async function logout() {
  await api("/api/logout", { method: "POST", body: "{}" }).catch(() => null);
  resetWorkspaceState();
  state.repositoryPrefix = "";
  els.scopeInput.value = "";
  updateDirectRepoPlaceholder();
  switchView(false);
  els.passwordInput.value = "";
}

function resetWorkspaceState() {
  state.repositories = [];
  state.repoNextLast = null;
  state.selectedRepo = null;
  state.tagCountByRepo.clear();
  state.loadingTagCountRepos.clear();
  state.tagCountGeneration += 1;
  state.tags = [];
  state.tagNextLast = null;
  state.metadataByTag.clear();
  state.loadingMetadataTags.clear();
  state.metadataGeneration += 1;
  state.selectedTags.clear();
  state.batchDeleting = false;
  state.repoStorageByRepo.clear();
  state.repoStorageLoading = false;
  state.repoStorageGeneration += 1;
}

async function applyScope(repositoryPrefix) {
  setBusy(els.applyScopeButton, true, "应用中");
  showNotice("正在切换镜像范围。", "loading");
  try {
    const payload = await api("/api/scope", {
      method: "POST",
      body: JSON.stringify({ repositoryPrefix }),
    });
    resetWorkspaceState();
    updateConnection(payload.connection);
    renderRepositories();
    renderTags();
    await loadCatalog(true);
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(els.applyScopeButton, false);
  }
}

async function loadCatalog(reset = false) {
  setBusy(els.refreshCatalogButton, true, "读取中");
  showNotice(
    state.repositoryPrefix
      ? `正在读取 ${state.repositoryPrefix} 下的镜像列表，仓库较大时会多扫描几页。`
      : "正在读取镜像列表。",
    "loading",
  );
  if (reset) setRepoListLoading("正在读取镜像列表");
  try {
    const last = reset ? "" : state.repoNextLast;
    const url = new URL("/api/catalog", window.location.origin);
    url.searchParams.set("limit", "100");
    if (last) url.searchParams.set("last", last);
    const payload = await api(`${url.pathname}${url.search}`);
    state.repositoryPrefix = normalizeRepositoryPrefix(payload.repositoryPrefix || state.repositoryPrefix);
    state.repositories = reset ? payload.repositories : [...state.repositories, ...payload.repositories];
    state.repoNextLast = payload.nextLast || null;
    if (reset && !state.repositories.includes(state.selectedRepo)) {
      state.selectedRepo = null;
      state.tags = [];
      state.metadataByTag.clear();
      state.loadingMetadataTags.clear();
      state.metadataGeneration += 1;
      state.selectedTags.clear();
    }
    renderRepositories();
    renderTags();
    showNotice(`已读取 ${state.repositories.length} 个镜像。`, "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(els.refreshCatalogButton, false);
  }
}

async function selectRepository(repo) {
  if (!state.repositories.includes(repo)) {
    state.repositories = [repo, ...state.repositories];
  }
  state.selectedRepo = repo;
  state.tags = [];
  state.tagNextLast = null;
  state.metadataByTag.clear();
  state.loadingMetadataTags.clear();
  state.metadataGeneration += 1;
  state.selectedTags.clear();
  state.repoStorageLoading = false;
  state.repoStorageGeneration += 1;
  renderRepositories();
  renderTags();
  await loadTags(true);
  loadRepositoryStorage(repo);
}

async function loadTags(reset = false) {
  if (!state.selectedRepo) return;
  setBusy(els.refreshTagsButton, true, "读取中");
  showNotice(`正在读取 ${state.selectedRepo} 的 tag 列表。`, "loading");
  if (reset) {
    state.tagNextLast = null;
    state.metadataByTag.clear();
    state.loadingMetadataTags.clear();
    state.metadataGeneration += 1;
    state.repoStorageByRepo.delete(state.selectedRepo);
    renderStorageSummary();
    setTagListLoading("正在读取 tag 列表");
  }
  try {
    const last = reset ? "" : state.tagNextLast;
    const repo = encodeURIComponent(state.selectedRepo);
    const url = new URL(`/api/repositories/${repo}/tags`, window.location.origin);
    url.searchParams.set("limit", "100");
    if (last) url.searchParams.set("last", last);
    const payload = await api(`${url.pathname}${url.search}`);
    state.tags = reset ? payload.tags : [...state.tags, ...payload.tags];
    state.selectedTags = new Set([...state.selectedTags].filter((tag) => state.tags.includes(tag)));
    state.tagNextLast = payload.nextLast || null;
    renderTags();
    showNotice(`已读取 ${state.tags.length} 个 tag，正在补充大小、digest 和时间。`, "loading");
    loadMetadataBatch();
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(els.refreshTagsButton, false);
  }
}

async function loadMetadata(tag, button) {
  if (!state.selectedRepo) return;
  if (state.loadingMetadataTags.has(tag)) return;
  const repoAtStart = state.selectedRepo;
  const generation = state.metadataGeneration;
  setBusy(button, true, "查询中");
  state.loadingMetadataTags.add(tag);
  renderTags();
  try {
    const repo = encodeURIComponent(repoAtStart);
    const encodedTag = encodeURIComponent(tag);
    const payload = await api(`/api/repositories/${repo}/tags/${encodedTag}/manifest`);
    if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
      state.metadataByTag.set(tag, payload);
    }
    if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
      renderTags();
    }
  } catch (error) {
    if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
      state.metadataByTag.set(tag, { error: true, timeNote: "详情读取失败" });
      showNotice(error.message, "error");
    }
  } finally {
    if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
      state.loadingMetadataTags.delete(tag);
      setBusy(button, false);
      renderTags();
    }
  }
}

async function loadMetadataBatch(limit = Number.POSITIVE_INFINITY) {
  const repoAtStart = state.selectedRepo;
  const generation = state.metadataGeneration;
  const candidates = state.tags
    .filter((tag) => !state.metadataByTag.has(tag) && !state.loadingMetadataTags.has(tag))
    .slice(0, limit);
  if (!repoAtStart || !candidates.length) {
    return;
  }

  candidates.forEach((tag) => state.loadingMetadataTags.add(tag));
  renderTags();
  let completed = 0;
  const total = candidates.length;
  const workers = Array.from({ length: Math.min(4, candidates.length) }, async () => {
    while (candidates.length && state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
      const tag = candidates.shift();
      try {
        const repo = encodeURIComponent(repoAtStart);
        const encodedTag = encodeURIComponent(tag);
        const payload = await api(`/api/repositories/${repo}/tags/${encodedTag}/manifest`);
        state.metadataByTag.set(tag, payload);
      } catch {
        state.metadataByTag.set(tag, { error: true, timeNote: "详情读取失败" });
      } finally {
        if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
          state.loadingMetadataTags.delete(tag);
          completed += 1;
          if (completed === total || completed % 5 === 0) {
            renderTags();
          }
        }
      }
    }
  });

  await Promise.all(workers);
  if (state.selectedRepo === repoAtStart && generation === state.metadataGeneration) {
    renderTags();
    showNotice(`已补充 ${completed} 个 tag 的大小、digest 和时间。`, "success");
  }
}

async function loadRepositoryStorage(repo = state.selectedRepo, manual = false) {
  if (!repo || state.repoStorageLoading) return;

  const generation = state.repoStorageGeneration + 1;
  state.repoStorageGeneration = generation;
  state.repoStorageLoading = true;
  renderStorageSummary();
  if (manual) {
    showNotice(`正在计算 ${repo} 的空间占用。`, "loading");
  }

  try {
    const encodedRepo = encodeURIComponent(repo);
    const url = new URL(`/api/repositories/${encodedRepo}/storage`, window.location.origin);
    url.searchParams.set("maxTags", "5000");
    url.searchParams.set("maxTagPages", "50");
    const payload = await api(`${url.pathname}${url.search}`);
    if (generation !== state.repoStorageGeneration || state.selectedRepo !== repo) return;
    state.repoStorageByRepo.set(repo, payload);
    renderStorageSummary();
    if (manual) {
      showNotice(`${repo} 空间计算完成：${formatStorageText(payload)}。`, "success");
    }
  } catch (error) {
    if (generation === state.repoStorageGeneration) {
      showNotice(error.message, "error");
    }
  } finally {
    if (generation === state.repoStorageGeneration) {
      state.repoStorageLoading = false;
      renderStorageSummary();
    }
  }
}

function invalidateStorageAfterDelete() {
  if (state.selectedRepo) {
    state.repoStorageByRepo.delete(state.selectedRepo);
  }
  renderStorageSummary();
}

async function deleteTag(tag, button) {
  if (!state.selectedRepo) return;
  const confirmed = window.confirm(`确认删除 ${state.selectedRepo}:${tag} 吗？`);
  if (!confirmed) return;

  setBusy(button, true, "删除中");
  showNotice("");
  try {
    const repo = encodeURIComponent(state.selectedRepo);
    const encodedTag = encodeURIComponent(tag);
    const payload = await api(`/api/repositories/${repo}/tags/${encodedTag}`, { method: "DELETE" });
    state.tags = state.tags.filter((item) => item !== tag);
    state.metadataByTag.delete(tag);
    state.loadingMetadataTags.delete(tag);
    state.selectedTags.delete(tag);
    invalidateStorageAfterDelete();
    renderTags();
    showNotice(`已删除 ${payload.repository}:${payload.tag}，digest ${payload.digest}`, "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function deleteSelectedTags() {
  if (!state.selectedRepo) return;
  const tagsToDelete = currentSelectedTags();
  if (!tagsToDelete.length) return;

  const confirmed = window.confirm(`确认删除 ${state.selectedRepo} 下选中的 ${tagsToDelete.length} 个 tag 吗？`);
  if (!confirmed) return;

  state.batchDeleting = true;
  setBusy(els.batchDeleteButton, true, "删除中");
  const failures = [];
  let successCount = 0;
  for (const tag of tagsToDelete) {
    showNotice(`正在删除 ${state.selectedRepo}:${tag}（${successCount + failures.length + 1}/${tagsToDelete.length}）。`, "loading");
    try {
      const repo = encodeURIComponent(state.selectedRepo);
      const encodedTag = encodeURIComponent(tag);
      await api(`/api/repositories/${repo}/tags/${encodedTag}`, { method: "DELETE" });
      successCount += 1;
      state.tags = state.tags.filter((item) => item !== tag);
      state.metadataByTag.delete(tag);
      state.loadingMetadataTags.delete(tag);
      state.selectedTags.delete(tag);
      invalidateStorageAfterDelete();
      renderTags();
    } catch (error) {
      failures.push({ tag, message: error.message });
    }
  }

  state.batchDeleting = false;
  setBusy(els.batchDeleteButton, false);
  renderTags();
  if (failures.length) {
    const sample = failures.slice(0, 3).map((item) => `${item.tag}: ${item.message}`).join("；");
    showNotice(`批量删除完成：成功 ${successCount} 个，失败 ${failures.length} 个。${sample}`, "error");
  } else {
    showNotice(`批量删除完成：成功删除 ${successCount} 个 tag。`, "success");
  }
}

async function boot() {
  switchView(false);
  renderStorageSummary();
  try {
    const payload = await api("/api/me");
    if (payload.authenticated) {
      updateConnection(payload.connection);
      switchView(true);
      await loadCatalog(true);
    }
  } catch {
    switchView(false);
  }
}

els.loginForm.addEventListener("submit", login);
els.logoutButton.addEventListener("click", logout);
els.scopeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  applyScope(els.scopeInput.value);
});
els.clearScopeButton.addEventListener("click", () => {
  els.scopeInput.value = "";
  applyScope("");
});
els.refreshCatalogButton.addEventListener("click", () => loadCatalog(true));
els.refreshTagsButton.addEventListener("click", async () => {
  await loadTags(true);
  loadRepositoryStorage(state.selectedRepo);
});
els.batchDeleteButton.addEventListener("click", deleteSelectedTags);
els.selectAllTagsInput.addEventListener("change", (event) => toggleAllTagSelection(event.currentTarget.checked));
els.loadMoreReposButton.addEventListener("click", () => loadCatalog(false));
els.loadMoreTagsButton.addEventListener("click", () => loadTags(false));
els.refreshRepoStorageButton.addEventListener("click", () => loadRepositoryStorage(state.selectedRepo, true));
els.repoSearch.addEventListener("input", renderRepositories);
els.directRepoForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const repo = qualifyRepository(els.directRepoInput.value);
  if (repo) selectRepository(repo);
});

boot();
