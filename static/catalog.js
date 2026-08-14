"use strict";

(() => {
  function loadFavorites() {
    try {
      const value = JSON.parse(localStorage.getItem("kb:favorites") || "[]");
      return new Set(Array.isArray(value) ? value : []);
    } catch (_error) {
      localStorage.removeItem("kb:favorites");
      return new Set();
    }
  }

  const Store = {
    catalog: null,
    currentPath: "",
    query: "",
    type: "",
    favoritesOnly: false,
    sort: "default",
    favorites: loadFavorites(),
    indexes: {
      byPath: new Map(),
      byId: new Map(),
      childrenByParent: new Map(),
      searchTextById: new Map(),
    },
  };

  const byId = (id) => document.getElementById(id);
  const create = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const fileUrl = (path) => "/files/" + path.split("/").map(encodeURIComponent).join("/");
  const normalize = (value) => String(value || "").normalize("NFKC").toLocaleLowerCase("zh-CN");
  const folderByPath = (path) => Store.indexes.byPath.get(path);

  function rebuildFolderIndexes() {
    const byPath = new Map();
    const byId = new Map();
    const childrenByParent = new Map();
    const searchTextById = new Map();
    Store.catalog.folders.forEach((folder) => {
      byPath.set(folder.path, folder);
      byId.set(folder.id, folder);
      searchTextById.set(folder.id, folderSearchText(folder));
      const children = childrenByParent.get(folder.parent_path) || [];
      children.push(folder);
      childrenByParent.set(folder.parent_path, children);
    });
    Store.indexes = { byPath, byId, childrenByParent, searchTextById };
  }

  function folderSearchText(folder) {
    return normalize([
      folder.title, folder.name, folder.path, folder.summary, folder.description,
      ...(folder.tags || []), ...folder.files.map((file) => file.name),
    ].join(" "));
  }

  function visibleFolders() {
    if (!Store.catalog) return [];
    const query = normalize(Store.query).trim();
    const source = query ? Store.catalog.folders : (Store.indexes.childrenByParent.get(Store.currentPath) || []);
    let folders = source.filter((folder) => {
      if (Store.type && !(folder.descendant_file_types || folder.file_types || []).includes(Store.type)) return false;
      if (Store.favoritesOnly && !Store.favorites.has(folder.id)) return false;
      if (query && !(Store.indexes.searchTextById.get(folder.id) || "").includes(query)) return false;
      return true;
    });
    if (Store.sort === "title") folders.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
    if (Store.sort === "modified") folders.sort((a, b) => new Date(b.modified) - new Date(a.modified));
    return folders;
  }

  function renderStats() {
    const stats = Store.catalog.stats;
    byId("itemCount").textContent = stats.folders ?? stats.items;
    byId("fileCount").textContent = stats.files;
    byId("categoryCount").textContent = stats.max_depth || stats.categories;
    byId("itemCountLabel").textContent = "知识目录";
    byId("categoryCountLabel").textContent = "目录层级";
    byId("siteTitle").firstChild.textContent = "把资料放对位置，";
    byId("siteSubtitle").textContent = Store.catalog.site.subtitle || "把散落的资料，整理成可抵达的知识坐标。";
    document.title = Store.catalog.site.title || "拾页星图 · 本地知识库";
    byId("lastUpdated").textContent = `最近同步 ${new Date(Store.catalog.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  }

  function navigationButton(folder) {
    const button = create("button", "category-button" + (Store.currentPath === folder.path ? " active" : ""));
    button.type = "button";
    button.dataset.folderPath = folder.path;
    button.setAttribute("aria-pressed", String(Store.currentPath === folder.path));
    button.append(
      create("span", "category-icon", folder.icon),
      create("span", "category-title", folder.title),
      create("span", "category-count", String(folder.descendant_file_count))
    );
    return button;
  }

  function renderCategories() {
    const list = byId("categoryList");
    const fragment = document.createDocumentFragment();
    const root = { path: "", icon: "⌂", title: "全部目录", descendant_file_count: Store.catalog.stats.files };
    fragment.append(navigationButton(root));
    (Store.indexes.childrenByParent.get("") || []).forEach((folder) => fragment.append(navigationButton(folder)));
    list.replaceChildren(fragment);
  }

  function renderBreadcrumbs() {
    const host = byId("breadcrumbs");
    const fragment = document.createDocumentFragment();
    const root = create("button", "breadcrumb", "知识库");
    root.type = "button"; root.dataset.folderPath = "";
    fragment.append(root);
    if (Store.currentPath) {
      const parts = Store.currentPath.split("/");
      parts.forEach((_part, index) => {
        fragment.append(create("span", "breadcrumb-separator", "›"));
        const path = parts.slice(0, index + 1).join("/");
        const folder = folderByPath(path);
        const button = create("button", "breadcrumb", folder?.title || parts[index]);
        button.type = "button"; button.dataset.folderPath = path;
        if (path === Store.currentPath) button.setAttribute("aria-current", "page");
        fragment.append(button);
      });
    }
    host.replaceChildren(fragment);
  }

  function renderTypes() {
    const host = byId("typeFilters");
    const fragment = document.createDocumentFragment();
    const all = create("button", "type-filter" + (!Store.type ? " active" : ""), "全部格式");
    all.type = "button"; all.dataset.type = "";
    fragment.append(all);
    Object.entries(Store.catalog.types).forEach(([type, count]) => {
      const button = create("button", "type-filter" + (Store.type === type ? " active" : ""), `${type} · ${count}`);
      button.type = "button"; button.dataset.type = type;
      fragment.append(button);
    });
    host.replaceChildren(fragment);
  }

  function fileActionButton(label, file, mode, className = "") {
    const button = create("button", className, label);
    button.type = "button";
    button.dataset.action = "file-action";
    button.dataset.mode = mode;
    button.dataset.path = file.relative_path;
    button.setAttribute("aria-label", `${label}：${file.name}`);
    return button;
  }

  function fileActionMenu(file) {
    const details = create("details", "file-actions");
    const summary = create("summary", "file-actions-trigger", "打开");
    summary.setAttribute("aria-label", `选择 ${file.name} 的打开方式`);
    const menu = create("div", "file-action-menu");
    menu.setAttribute("role", "group");
    menu.setAttribute("aria-label", `${file.name} 的文件操作`);
    menu.append(
      fileActionButton("默认应用", file, "default"),
      fileActionButton("选择应用…", file, "choose"),
      fileActionButton("所在位置", file, "reveal")
    );
    details.append(summary, menu);
    return details;
  }

  function fileRow(file) {
    const row = create("div", "file-row");
    row.append(create("span", "file-type", file.extension.slice(0, 4)));
    const link = create("a", "file-link", file.name);
    link.href = fileUrl(file.relative_path); link.target = "_blank"; link.rel = "noopener";
    link.title = `在浏览器中查看：${file.name}`;
    row.append(link, fileActionMenu(file));
    return row;
  }

  function renderFolderCard(folder, index) {
    const card = create("article", "knowledge-card folder-card");
    card.dataset.folderId = folder.id;
    card.dataset.folderPath = folder.path;
    card.tabIndex = 0;
    card.setAttribute("aria-labelledby", `title-${folder.id}`);

    const top = create("div", "card-top");
    top.append(create("span", "card-code", `DIR · ${String(index + 1).padStart(3, "0")}`));
    const favorite = create("button", "favorite-button" + (Store.favorites.has(folder.id) ? " active" : ""), Store.favorites.has(folder.id) ? "★" : "☆");
    favorite.type = "button"; favorite.dataset.action = "favorite"; favorite.dataset.id = folder.id;
    favorite.setAttribute("aria-label", Store.favorites.has(folder.id) ? `取消收藏 ${folder.title}` : `收藏 ${folder.title}`);
    top.append(favorite);
    const pathLabel = folder.parent_path ? folder.parent_path.replaceAll("/", " › ") : "根目录";
    card.append(top, create("p", "card-category", pathLabel.toUpperCase()));
    const title = create("h3", "", folder.title); title.id = `title-${folder.id}`; card.append(title);
    card.append(create("p", "card-summary", folder.summary));

    const tags = create("div", "tag-row");
    (folder.tags || []).slice(0, 4).forEach((tag) => tags.append(create("span", "tag", `# ${tag}`)));
    if (!folder.tags.length) folder.file_types.slice(0, 3).forEach((type) => tags.append(create("span", "tag", type.toUpperCase())));
    card.append(tags);

    const files = create("div", "file-preview");
    folder.files.slice(0, 2).forEach((file) => files.append(fileRow(file)));
    if (!folder.files.length) files.append(create("p", "result-summary", folder.child_count ? `包含 ${folder.child_count} 个子目录` : "此目录暂无直属文件"));
    card.append(files);

    const footer = create("div", "card-footer");
    footer.append(create("span", "", `${folder.child_count} 个子目录 · ${folder.files.length} 个直属文件`));
    const open = create("button", "detail-button", folder.child_count ? "进入目录 →" : "查看目录 →");
    open.type = "button"; open.dataset.action = "enter-folder"; open.dataset.path = folder.path;
    footer.append(open); card.append(footer);
    return card;
  }

  function renderCurrentFolder() {
    const panel = byId("currentFolderPanel");
    const folder = folderByPath(Store.currentPath);
    if (!folder || Store.query) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    byId("currentFolderTitle").textContent = folder.title;
    byId("currentFolderDescription").textContent = folder.description;
    const files = byId("currentFolderFiles");
    const fragment = document.createDocumentFragment();
    folder.files.forEach((file) => fragment.append(fileRow(file)));
    if (!folder.files.length) fragment.append(create("p", "result-summary", "此目录暂无直属文件。"));
    files.replaceChildren(fragment);
  }

  function renderCatalog() {
    const folders = visibleFolders();
    const grid = byId("cardGrid");
    const fragment = document.createDocumentFragment();
    folders.forEach((folder, index) => fragment.append(renderFolderCard(folder, index)));
    grid.replaceChildren(fragment);
    grid.setAttribute("aria-busy", "false");
    grid.hidden = folders.length === 0;
    byId("emptyState").hidden = folders.length !== 0;
    byId("visibleCount").textContent = folders.length;
    const current = folderByPath(Store.currentPath);
    byId("catalogTitle").textContent = Store.query ? "全库搜索结果" : (current?.title || "全部目录");
    const filters = [Store.query && `关键词“${Store.query}”`, Store.type && `${Store.type.toUpperCase()} 格式`, Store.favoritesOnly && "已收藏"].filter(Boolean);
    const scope = Store.query ? "全库" : (current ? `“${current.title}”的直属目录` : "根目录");
    byId("resultSummary").textContent = `${scope}显示 ${folders.length} 个目录${filters.length ? ` · ${filters.join(" · ")}` : ""}`;
    renderBreadcrumbs(); renderCurrentFolder();
  }

  function renderAll() {
    if (!Store.catalog) return;
    rebuildFolderIndexes();
    renderStats(); renderCategories(); renderTypes(); renderCatalog();
  }

  function enterFolder(path) {
    Store.currentPath = path || "";
    Store.query = "";
    byId("searchInput").value = "";
    byId("clearSearch").hidden = true;
    renderCategories(); renderCatalog();
    byId("content").focus({ preventScroll: true });
  }

  function findFolder(id) { return Store.indexes.byId.get(id); }

  window.KB = {
    Store, byId, renderAll, renderCategories, renderTypes, renderCatalog, findFolder, enterFolder,
  };
})();
