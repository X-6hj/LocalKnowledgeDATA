"use strict";

(() => {
  const { Store, byId, renderAll, renderCategories, renderGlobalStructure, renderTypes, renderCatalog, findFolder, enterFolder } = window.KB;
  let toastTimer = 0;
  let refreshTimer = 0;
  let hoveredId = "";
  let tooltipHideTimer = 0;

  function setConnection(kind, text) {
    const state = byId("syncState");
    state.classList.remove("online", "error");
    if (kind) state.classList.add(kind);
    state.lastChild.textContent = ` ${text}`;
  }

  function toast(message) {
    const element = byId("toast");
    element.textContent = message;
    element.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { element.hidden = true; }, 2800);
  }

  async function loadCatalog(initial = false) {
    try {
      const response = await fetch("/api/catalog", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      const incoming = payload.data;
      if (incoming.schema_version !== 2 || !Array.isArray(incoming.folders)) {
        throw new Error("服务端目录接口版本过旧，请重新启动知识库服务");
      }
      const changed = !Store.catalog || Store.catalog.revision !== incoming.revision;
      Store.catalog = incoming;
      setConnection("online", "已同步");
      if (changed || initial) {
        renderAll();
        if (!initial && changed) toast("检测到资料变动，索引已自动更新");
      } else {
        byId("lastUpdated").textContent = `最近检查 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
      }
      window.clearTimeout(refreshTimer);
      const seconds = Math.max(2, Number(incoming.site.refresh_seconds) || 5);
      refreshTimer = window.setTimeout(() => loadCatalog(false), seconds * 1000);
    } catch (error) {
      setConnection("error", "连接中断");
      byId("resultSummary").textContent = `无法读取知识库：${error.message}`;
      window.clearTimeout(refreshTimer);
      refreshTimer = window.setTimeout(() => loadCatalog(false), 5000);
    }
  }

  async function runFileAction(path, action = "default") {
    const successMessages = {
      default: "已交给系统默认应用打开",
      choose: "已打开 Windows 应用选择器",
      reveal: "已在资源管理器中定位文件",
    };
    try {
      const response = await fetch("/api/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, action }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "文件操作失败");
      toast(successMessages[action] || "文件操作已执行");
    } catch (error) {
      toast(`无法执行：${error.message}`);
    }
  }

  function saveFavorites() {
    localStorage.setItem("kb:favorites", JSON.stringify([...Store.favorites]));
  }

  function toggleFavorite(id) {
    if (Store.favorites.has(id)) Store.favorites.delete(id);
    else Store.favorites.add(id);
    saveFavorites();
    renderCatalog();
    toast(Store.favorites.has(id) ? "已加入收藏" : "已取消收藏");
  }

  function resetFilters() {
    Store.query = "";
    Store.currentPath = "";
    Store.type = "";
    Store.favoritesOnly = false;
    byId("searchInput").value = "";
    byId("clearSearch").hidden = true;
    byId("favoriteFilter").setAttribute("aria-pressed", "false");
    renderCategories(); renderTypes(); renderCatalog();
  }

  function positionTooltip(card) {
    const tooltip = byId("hoverCard");
    const rect = card.getBoundingClientRect();
    const width = Math.min(390, window.innerWidth - 32);
    const rightSpace = window.innerWidth - rect.right;
    const left = rightSpace >= width + 12 ? rect.right + 8 : Math.max(16, rect.left - width - 8);
    const top = Math.min(Math.max(16, rect.top + 8), window.innerHeight - Math.min(340, tooltip.offsetHeight) - 16);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function cancelTooltipHide() {
    window.clearTimeout(tooltipHideTimer);
    tooltipHideTimer = 0;
  }

  function showTooltip(card) {
    if (window.matchMedia("(hover: none)").matches) return;
    const folder = findFolder(card.dataset.folderId);
    if (!folder) return;
    cancelTooltipHide();
    hoveredId = folder.id;
    byId("hoverTitle").textContent = folder.title;
    byId("hoverDescription").textContent = folder.description;
    byId("hoverMeta").textContent = `${folder.child_count} 个子目录 · ${folder.files.length} 个直属文件 · 第 ${folder.depth} 层`;
    const tooltip = byId("hoverCard");
    tooltip.hidden = false;
    requestAnimationFrame(() => positionTooltip(card));
  }

  function hideTooltip() {
    cancelTooltipHide();
    hoveredId = "";
    byId("hoverCard").hidden = true;
  }

  function scheduleTooltipHide() {
    cancelTooltipHide();
    tooltipHideTimer = window.setTimeout(hideTooltip, 320);
  }

  function setupFilters() {
    byId("categoryList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder-path]");
      if (!button) return;
      enterFolder(button.dataset.folderPath); hideTooltip();
    });
    byId("breadcrumbs").addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder-path]");
      if (!button) return;
      enterFolder(button.dataset.folderPath); hideTooltip();
    });
    byId("typeFilters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-type]");
      if (!button) return;
      Store.type = button.dataset.type;
      renderTypes(); renderCatalog(); hideTooltip();
    });
    byId("searchInput").addEventListener("input", (event) => {
      Store.query = event.target.value;
      byId("clearSearch").hidden = !Store.query;
      renderCatalog(); hideTooltip();
    });
    byId("searchForm").addEventListener("submit", (event) => event.preventDefault());
    byId("clearSearch").addEventListener("click", () => {
      Store.query = ""; byId("searchInput").value = ""; byId("clearSearch").hidden = true;
      renderCatalog(); byId("searchInput").focus();
    });
    byId("favoriteFilter").addEventListener("click", (event) => {
      Store.favoritesOnly = !Store.favoritesOnly;
      event.currentTarget.setAttribute("aria-pressed", String(Store.favoritesOnly));
      renderCatalog();
    });
    byId("sortSelect").addEventListener("change", (event) => { Store.sort = event.target.value; renderCatalog(); });
    byId("resetFilters").addEventListener("click", resetFilters);
  }

  function setupGlobalStructure() {
    const dialog = byId("structureDialog");
    const opener = byId("structureOpen");
    const search = byId("structureSearch");
    let initialized = false;

    const expandCurrentPath = () => {
      let path = Store.currentPath;
      while (path) {
        Store.structureExpanded.add(path);
        path = Store.indexes.byPath.get(path)?.parent_path || "";
      }
    };

    opener.addEventListener("click", () => {
      hideTooltip();
      if (!initialized) {
        Store.catalog.folders.forEach((folder) => {
          if (folder.child_count) Store.structureExpanded.add(folder.path);
        });
        initialized = true;
      } else {
        expandCurrentPath();
      }
      dialog.showModal();
      renderGlobalStructure();
      search.focus();
    });

    search.addEventListener("input", (event) => {
      Store.structureQuery = event.target.value;
      renderGlobalStructure();
    });

    byId("structureFileToggle").addEventListener("change", (event) => {
      Store.structureShowFiles = event.target.checked;
      if (Store.structureShowFiles) {
        Store.catalog.folders.forEach((folder) => {
          if (folder.files.length) Store.structureExpanded.add(folder.path);
        });
      }
      renderGlobalStructure();
    });

    byId("structureExpandCurrent").addEventListener("click", () => {
      expandCurrentPath();
      renderGlobalStructure();
      const current = dialog.querySelector('[aria-current="page"]');
      current?.scrollIntoView({ block: "center", behavior: "smooth" });
      current?.focus({ preventScroll: true });
    });

    byId("structureExpandAll").addEventListener("click", () => {
      Store.catalog.folders.forEach((folder) => {
        if (folder.child_count || (Store.structureShowFiles && folder.files.length)) {
          Store.structureExpanded.add(folder.path);
        }
      });
      renderGlobalStructure();
    });

    byId("structureCollapseAll").addEventListener("click", () => {
      Store.structureExpanded.clear();
      Store.structureQuery = "";
      search.value = "";
      renderGlobalStructure();
      search.focus();
    });

    byId("structureTree").addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]");
      if (!action) return;
      if (action.dataset.action === "structure-toggle") {
        const path = action.dataset.path;
        if (Store.structureExpanded.has(path)) Store.structureExpanded.delete(path);
        else Store.structureExpanded.add(path);
        renderGlobalStructure();
        dialog.querySelector(`[data-action="structure-toggle"][data-path="${CSS.escape(path)}"]`)?.focus();
      }
      if (action.dataset.action === "structure-enter") {
        enterFolder(action.dataset.path);
        dialog.close();
      }
    });

    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      Store.structureQuery = "";
      search.value = "";
      byId("structureTree").replaceChildren();
      opener.focus();
    });
  }

  function setupCards() {
    const grid = byId("cardGrid");
    const closeFileMenus = (except = null) => {
      document.querySelectorAll("details.file-actions[open]").forEach((details) => {
        if (details !== except) details.removeAttribute("open");
      });
    };
    grid.addEventListener("click", (event) => {
      const summary = event.target.closest(".file-actions > summary");
      if (summary) {
        closeFileMenus(summary.parentElement);
        return;
      }
      const action = event.target.closest("[data-action]");
      if (!action) return;
      event.stopPropagation();
      if (action.dataset.action === "file-action") {
        action.closest("details")?.removeAttribute("open");
        runFileAction(action.dataset.path, action.dataset.mode);
      }
      if (action.dataset.action === "favorite") toggleFavorite(action.dataset.id);
      if (action.dataset.action === "enter-folder") enterFolder(action.dataset.path);
    });
    const runDelegatedFileAction = (event) => {
      const action = event.target.closest('[data-action="file-action"]');
      if (action) runFileAction(action.dataset.path, action.dataset.mode);
    };
    byId("currentFolderFiles").addEventListener("click", runDelegatedFileAction);
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".file-actions")) closeFileMenus();
    });
    grid.addEventListener("pointerover", (event) => {
      const card = event.target.closest(".folder-card");
      if (!card) return;
      cancelTooltipHide();
      if (card.dataset.folderId === hoveredId && !byId("hoverCard").hidden) return;
      showTooltip(card);
    });
    grid.addEventListener("pointerout", (event) => {
      const card = event.target.closest(".folder-card");
      if (!card || card.contains(event.relatedTarget)) return;
      if (event.relatedTarget && byId("hoverCard").contains(event.relatedTarget)) return;
      scheduleTooltipHide();
    });
    grid.addEventListener("focusin", (event) => {
      const card = event.target.closest(".folder-card");
      if (card) showTooltip(card);
    });
    grid.addEventListener("focusout", (event) => {
      const card = event.target.closest(".folder-card");
      if (!card || card.contains(event.relatedTarget)) return;
      scheduleTooltipHide();
    });
    const tooltip = byId("hoverCard");
    tooltip.addEventListener("pointerenter", cancelTooltipHide);
    tooltip.addEventListener("pointerleave", (event) => {
      if (tooltip.contains(event.relatedTarget)) return;
      scheduleTooltipHide();
    });
  }

  function setupDialogs() {
    byId("helpButton").addEventListener("click", () => byId("helpDialog").showModal());
    const dialog = byId("helpDialog");
    dialog.addEventListener("click", (event) => {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
  }

  function setupTheme() {
    const saved = localStorage.getItem("kb:theme");
    const theme = saved || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.dataset.theme = theme;
    byId("themeToggle").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("kb:theme", next);
      toast(next === "dark" ? "已切换到深色主题" : "已切换到浅色主题");
    });
  }

  function setupKeyboard() {
    document.addEventListener("keydown", (event) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (event.key === "/" && !typing) { event.preventDefault(); byId("searchInput").focus(); }
      if (event.key === "Escape") hideTooltip();
    });
  }

  async function init() {
    setupTheme(); setupFilters(); setupGlobalStructure(); setupCards(); setupDialogs(); setupKeyboard();
    await loadCatalog(true);
  }

  init();
})();
