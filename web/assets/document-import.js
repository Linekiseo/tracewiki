(() => {
  "use strict";

  const SUPPORTED_EXTENSIONS = new Set(["md", "markdown", "txt", "html", "htm", "pdf", "docx"]);
  const importerState = {
    mode: "files",
    files: [],
    installed: false,
    busy: false,
  };

  const qs = selector => document.querySelector(selector);
  const qsa = selector => [...document.querySelectorAll(selector)];
  const escapeHtml = (value = "") => String(value).replace(
    /[&<>'"]/g,
    character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]),
  );
  const fileExtension = name => String(name || "").split(".").at(-1)?.toLowerCase() || "";
  const fileTitle = name => {
    const value = String(name || "Untitled document").replace(/\.[^.]+$/, "");
    return value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || "Untitled document";
  };
  const formatBytes = bytes => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10_240 ? 1 : 0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };
  const statusLabel = status => ({
    ready:"待处理",
    invalid:"需处理",
    uploading:"上传中",
    ingested:"已导入",
    existing:"已存在",
    failed:"失败",
  })[status] || status;

  function validateFile(file) {
    const extension = fileExtension(file.name);
    if (!SUPPORTED_EXTENSIONS.has(extension)) return "不支持此文件格式";
    if (!file.size) return "文件内容为空";
    return "";
  }

  function addFiles(fileList) {
    if (
      importerState.files.length
      && importerState.files.every(item => ["ingested", "existing"].includes(item.status))
    ) {
      importerState.files = [];
    }
    const existing = new Set(importerState.files.map(item => item.key));
    const incoming = [...fileList];
    let duplicateCount = 0;
    incoming.forEach(file => {
      const key = `${file.name}\u001f${file.size}\u001f${file.lastModified}`;
      if (existing.has(key)) {
        duplicateCount += 1;
        return;
      }
      const error = validateFile(file);
      importerState.files.push({
        key,
        file,
        title:fileTitle(file.name),
        status:error ? "invalid" : "ready",
        error,
      });
      existing.add(key);
    });
    renderQueue();
    if (duplicateCount) {
      setSelectionSummary(`已忽略 ${duplicateCount} 个重复文件`);
    }
  }

  function renderQueue() {
    const queue = qs("#document-upload-queue");
    if (!queue) return;
    const readyCount = importerState.files.filter(item => item.status === "ready" && !item.error).length;
    const completedCount = importerState.files.filter(item => ["ingested", "existing"].includes(item.status)).length;
    const failedCount = importerState.files.filter(item => ["failed", "invalid"].includes(item.status)).length;
    const totalBytes = importerState.files.reduce((total, item) => total + item.file.size, 0);
    qs("#document-file-count").textContent = importerState.files.length;
    qs("#document-queue-summary").textContent = importerState.files.length
      ? (completedCount === importerState.files.length
        ? `${completedCount} 个已处理 · ${formatBytes(totalBytes)}`
        : `${readyCount} 个待处理${failedCount ? ` · ${failedCount} 个需处理` : ""} · ${formatBytes(totalBytes)}`)
      : "尚未选择文件";
    qs("#document-clear-files").disabled = importerState.busy || !importerState.files.length;
    if (!importerState.files.length) {
      queue.innerHTML = '<div class="document-queue-empty"><i class="ph ph-file-dashed"></i><strong>还没有待导入文档</strong><span>可一次选择不同格式，文件标题可在队列中逐项调整。</span></div>';
      updateSubmitState();
      return;
    }
    queue.innerHTML = importerState.files.map((item, index) => {
      const extension = fileExtension(item.file.name);
      const mark = extension === "markdown" ? "MD" : extension.toUpperCase();
      return `<article class="document-queue-item" data-document-queue-item="${index}">
        <span class="document-file-mark type-${escapeHtml(extension)}">${escapeHtml(mark)}</span>
        <span class="document-file-meta"><strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong><small>${formatBytes(item.file.size)} · ${escapeHtml(extension.toUpperCase())}</small></span>
        <input type="text" value="${escapeHtml(item.title)}" data-document-title="${index}" aria-label="${escapeHtml(item.file.name)} 的文档标题" ${importerState.busy || ["ingested", "existing"].includes(item.status) ? "disabled" : ""} />
        <span class="document-file-state ${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
        <button class="document-file-remove" type="button" data-document-remove="${index}" aria-label="移除 ${escapeHtml(item.file.name)}" ${importerState.busy ? "disabled" : ""}><i class="ph ph-x"></i></button>
        ${item.error ? `<span class="document-queue-error">${escapeHtml(item.error)}</span>` : ""}
      </article>`;
    }).join("");
    setSelectionSummary(
      completedCount === importerState.files.length
        ? `${completedCount} 个文档已完成处理`
        : `${readyCount} 个待处理${completedCount ? ` · ${completedCount} 个已完成` : ""}${failedCount ? ` · ${failedCount} 个需处理` : ""}`,
    );
    updateSubmitState();
  }

  function modeInputReady() {
    if (importerState.mode === "files") {
      return importerState.files.length > 0
        && importerState.files.every(item => item.status === "ready" && !item.error && item.title.trim());
    }
    if (importerState.mode === "paths") {
      const paths = pathValues();
      return paths.length > 0;
    }
    return Boolean(
      qs('#document-form [name="inline_title"]').value.trim()
      && qs('#document-form [name="inline_content"]').value.trim()
    );
  }

  function updateSubmitState() {
    const submit = qs("#document-submit");
    if (!submit) return;
    submit.disabled = importerState.busy || !modeInputReady();
    if (importerState.busy) return;
    const labels = {
      files:`导入 ${importerState.files.length} 个文件`,
      paths:`接入 ${pathValues().length} 个路径`,
      text:"接入文本",
    };
    submit.innerHTML = `<i class="ph ph-play"></i> ${labels[importerState.mode]}`;
    if (importerState.mode === "paths") {
      const count = pathValues().length;
      setSelectionSummary(count ? `${count} 个服务器文件待接入` : "每行填写一个服务器文件路径");
    } else if (importerState.mode === "text") {
      setSelectionSummary("正文将作为单独的文档版本接入");
    }
  }

  function setMode(mode) {
    importerState.mode = mode;
    qsa("[data-document-mode]").forEach(button => {
      const active = button.dataset.documentMode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    qsa("[data-document-panel]").forEach(panel => {
      const active = panel.dataset.documentPanel === mode;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
    hideError();
    updateSubmitState();
  }

  function pathValues() {
    return [...new Set(
      String(qs('#document-form [name="source_paths"]')?.value || "")
        .split(/\r?\n/)
        .map(value => value.trim())
        .filter(Boolean),
    )];
  }

  function sharedFields() {
    const form = qs("#document-form");
    return {
      version:form.elements.version.value.trim() || "v1",
      iteration_id:form.elements.iteration_id.value || "",
      authors:form.elements.authors.value.trim(),
      tags:form.elements.tags.value.trim(),
      extract_claims:form.elements.extract_claims.checked,
    };
  }

  function authHeaders(headers = {}) {
    const token = localStorage.getItem("rag_api_token");
    const aclRefs = localStorage.getItem("rag_acl_refs");
    if (token) headers.Authorization = `Bearer ${token}`;
    if (aclRefs) headers["X-RAG-ACL-Refs"] = aclRefs;
    return headers;
  }

  function uploadBatch(formData, onProgress) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/v1/documents/upload-batch");
      Object.entries(authHeaders()).forEach(([name, value]) => request.setRequestHeader(name, value));
      request.upload.addEventListener("progress", event => {
        if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 88));
      });
      request.addEventListener("load", () => {
        let payload = {};
        try { payload = JSON.parse(request.responseText || "{}"); } catch {}
        if (request.status >= 200 && request.status < 300) resolve(payload);
        else reject(new Error(payload.detail || `${request.status} ${request.statusText}`));
      });
      request.addEventListener("error", () => reject(new Error("文档上传网络请求失败")));
      request.send(formData);
    });
  }

  async function jsonRequest(payload) {
    const response = await fetch("/v1/documents/ingest", {
      method:"POST",
      headers:authHeaders({"Content-Type":"application/json"}),
      body:JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `${response.status} ${response.statusText}`);
    return result;
  }

  function showProgress(label, value, detail) {
    const section = qs("#document-import-progress");
    section.hidden = false;
    qs("#document-progress-label").textContent = label;
    qs("#document-progress-value").textContent = `${value}%`;
    qs("#document-progress-bar").value = value;
    qs("#document-progress-detail").textContent = detail;
  }

  function setBusy(busy) {
    importerState.busy = busy;
    qsa('#document-dialog [data-close-modal]').forEach(button => { button.disabled = busy; });
    qsa("[data-document-mode]").forEach(button => { button.disabled = busy; });
    renderQueue();
    updateSubmitState();
  }

  function showError(message) {
    const error = qs("#document-error");
    error.textContent = message;
    error.hidden = false;
  }
  function hideError() {
    const error = qs("#document-error");
    error.hidden = true;
    error.textContent = "";
  }
  function setSelectionSummary(message) {
    qs("#document-selection-summary").textContent = message;
  }

  async function submitFiles() {
    const fields = sharedFields();
    const payload = new FormData();
    importerState.files.forEach(item => {
      item.status = "uploading";
      item.error = "";
      payload.append("files", item.file, item.file.name);
    });
    payload.append("version", fields.version);
    payload.append("iteration_id", fields.iteration_id);
    payload.append("authors", fields.authors);
    payload.append("tags", fields.tags);
    payload.append("extract_claims", String(fields.extract_claims));
    payload.append("manifest", JSON.stringify(importerState.files.map(item => ({
      title:item.title.trim(),
      version:fields.version,
    }))));
    renderQueue();
    showProgress("正在上传文档", 2, `正在传输 ${importerState.files.length} 个文件`);
    const result = await uploadBatch(payload, value => showProgress("正在上传文档", value, "文件传输中，请保持窗口打开"));
    showProgress("正在解析与建立索引", 94, "提取章节、表格、图片、引用与 Claim");
    result.items.forEach((responseItem, index) => {
      const queueItem = importerState.files[index];
      if (!queueItem) return;
      queueItem.status = responseItem.status;
      queueItem.error = responseItem.error || "";
    });
    renderQueue();
    showProgress(
      result.failed ? "批次处理完成，部分文件失败" : "批次导入完成",
      100,
      `${result.succeeded} 个成功 · ${result.failed} 个失败 · 批次 ${result.batch_id.slice(0, 8)}`,
    );
    setSelectionSummary(`${result.succeeded} 个文档已接入${result.failed ? ` · ${result.failed} 个需要处理` : ""}`);
    return result;
  }

  async function submitPaths() {
    const fields = sharedFields();
    const paths = pathValues();
    const results = [];
    for (const [index, source] of paths.entries()) {
      const title = fileTitle(source.split("/").at(-1));
      showProgress("正在接入服务器文档", Math.round(index / paths.length * 94), `${index + 1} / ${paths.length} · ${source}`);
      try {
        const document = await jsonRequest({
          title,
          version:fields.version,
          iteration_id:fields.iteration_id || null,
          source,
          content:null,
          authors:fields.authors.split(",").map(value => value.trim()).filter(Boolean),
          tags:fields.tags.split(",").map(value => value.trim()).filter(Boolean),
          extract_claims:fields.extract_claims,
        });
        results.push({status:"ingested", document});
      } catch (error) {
        results.push({status:"failed", source, error:error.message});
      }
    }
    const failed = results.filter(item => item.status === "failed");
    showProgress(failed.length ? "批次处理完成，部分路径失败" : "服务器文档接入完成", 100, `${results.length - failed.length} 个成功 · ${failed.length} 个失败`);
    if (failed.length) showError(failed.map(item => `${item.source}：${item.error}`).join("\n"));
    return {succeeded:results.length - failed.length, failed:failed.length, items:results};
  }

  async function submitText() {
    const fields = sharedFields();
    const form = qs("#document-form");
    showProgress("正在解析文本", 42, "提取结构、表格、引用与 Claim");
    const document = await jsonRequest({
      title:form.elements.inline_title.value.trim(),
      version:fields.version,
      iteration_id:fields.iteration_id || null,
      source:null,
      content:form.elements.inline_content.value.trim(),
      authors:fields.authors.split(",").map(value => value.trim()).filter(Boolean),
      tags:fields.tags.split(",").map(value => value.trim()).filter(Boolean),
      extract_claims:fields.extract_claims,
    });
    showProgress("文本接入完成", 100, `${document.display_key} · ${document.claims.length} 个 Claim`);
    return {succeeded:1, failed:0, items:[{status:"ingested", document}]};
  }

  async function submit(event) {
    event.preventDefault();
    if (importerState.busy || !modeInputReady()) return;
    hideError();
    setBusy(true);
    try {
      const result = importerState.mode === "files"
        ? await submitFiles()
        : importerState.mode === "paths"
          ? await submitPaths()
          : await submitText();
      await window.refreshDocuments?.();
      await window.refreshWorkspace?.();
      window.showToast?.(`${result.succeeded} 个科研文档已接入${result.failed ? `，${result.failed} 个失败` : ""}`);
    } catch (error) {
      showError(error.message);
      showProgress("导入未完成", 100, "请检查错误后重试");
      if (importerState.mode === "files") {
        importerState.files.forEach(item => {
          if (item.status === "uploading") {
            item.status = "failed";
            item.error = error.message;
          }
        });
      }
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    const form = qs("#document-form");
    form.reset();
    form.elements.version.value = "v1";
    form.elements.extract_claims.checked = true;
    importerState.files = [];
    importerState.busy = false;
    qs("#document-import-progress").hidden = true;
    hideError();
    setMode("files");
    renderQueue();
  }

  function open() {
    install();
    reset();
    window.renderDocumentIterationOptions?.();
    qs("#document-dialog").showModal();
  }

  function install() {
    if (importerState.installed) return;
    importerState.installed = true;
    qsa("[data-document-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.documentMode)));
    const input = qs("#document-files");
    input.addEventListener("change", event => {
      addFiles(event.target.files);
      event.target.value = "";
    });
    const dropzone = qs("#document-dropzone");
    ["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault();
      if (!importerState.busy) dropzone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, event => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    }));
    dropzone.addEventListener("drop", event => {
      if (!importerState.busy) addFiles(event.dataTransfer.files);
    });
    dropzone.addEventListener("keydown", event => {
      if (["Enter", " "].includes(event.key) && !importerState.busy) {
        event.preventDefault();
        input.click();
      }
    });
    qs("#document-upload-queue").addEventListener("click", event => {
      const remove = event.target.closest("[data-document-remove]");
      if (!remove || importerState.busy) return;
      importerState.files.splice(Number(remove.dataset.documentRemove), 1);
      renderQueue();
    });
    qs("#document-upload-queue").addEventListener("input", event => {
      const title = event.target.closest("[data-document-title]");
      if (!title) return;
      importerState.files[Number(title.dataset.documentTitle)].title = title.value;
      updateSubmitState();
    });
    qs("#document-clear-files").addEventListener("click", () => {
      if (importerState.busy) return;
      importerState.files = [];
      renderQueue();
    });
    qsa('#document-form textarea, #document-form input[name="inline_title"], #document-form input[name="version"]')
      .forEach(control => control.addEventListener("input", updateSubmitState));
  }

  window.DocumentImporter = {
    open,
    submit,
    install,
    addFiles,
    setMode,
  };
})();
