/** Obsidian desktop interface for the local Badger JSON service. */
const { Component, MarkdownRenderer, MarkdownView, Modal, Notice, Plugin, PluginSettingTab, Setting, TFile, requestUrl } = require("obsidian");

const DEFAULTS = Object.freeze({
  serverUrl: "http://127.0.0.1:8765",
  noteFolder: "Zettelkasten",
  apiToken: "",
  topK: 10,
});

/** Preserve full file names in change notices. */
function changes(status, afterIndex = false) {
  const lines = [];
  if (status.rebuild) lines.push(afterIndex ? "A full index build was performed." : "A full index build is needed.");
  for (const [label, items] of [["New", status.new], ["Changed", status.changed], ["Removed", status.removed]]) {
    for (const name of items || []) lines.push(`${label}: ${name}`);
  }
  if (!lines.length) lines.push("All indexed notes are current.");
  return lines;
}

/** Handle JSON replies, including a 409 response with stale source names. */
class BadgerClient {
  constructor(plugin) {
    this.plugin = plugin;
  }

  async request(path, method = "GET", body = null) {
    const settings = this.plugin.settings;
    const headers = { "Content-Type": "application/json" };
    if (settings.apiToken) headers.Authorization = `Bearer ${settings.apiToken}`;
    let response;
    try {
      response = await requestUrl({
        url: `${settings.serverUrl.replace(/\/+$/, "")}${path}`,
        method,
        headers,
        body: body === null ? undefined : JSON.stringify(body),
        throw: false,
      });
    } catch (error) {
      throw new Error(`Cannot reach Badger. Start 'badger serve' and check its URL. ${error.message || error}`);
    }
    let data;
    try {
      data = JSON.parse(response.text);
    } catch (_) {
      throw new Error(`Badger returned an invalid response (HTTP ${response.status}).`);
    }
    if (response.status < 200 || response.status >= 300) {
      const failure = new Error(data.error || `Badger returned HTTP ${response.status}.`);
      failure.status = data.status;
      throw failure;
    }
    return data;
  }
}

/** Show every file name involved in an index update or stale response. */
class BadgerStatusModal extends Modal {
  constructor(plugin, title, status, extra = "", afterIndex = false) {
    super(plugin.app);
    this.title = title;
    this.status = status;
    this.extra = extra;
    this.afterIndex = afterIndex;
  }

  onOpen() {
    this.setTitle(this.title);
    this.contentEl.empty();
    if (this.extra) this.contentEl.createEl("p", { text: this.extra });
    const list = this.contentEl.createEl("ul");
    for (const line of changes(this.status, this.afterIndex)) list.createEl("li", { text: line });
  }
}

/** Search modal with full paragraph text and an explicit index action. */
class BadgerSearchModal extends Modal {
  constructor(plugin) {
    super(plugin.app);
    this.plugin = plugin;
    this.searchVersion = 0;
    this.renderComponent = null;
  }

  onOpen() {
    this.setTitle("Badger: find related notes");
    this.contentEl.empty();
    const form = this.contentEl.createEl("form", { cls: "badger-form" });
    this.input = form.createEl("input", {
      type: "text",
      placeholder: "An idea, concept, or a few words",
      cls: "badger-query",
    });
    form.createEl("button", { type: "submit", text: "Search", cls: "mod-cta" });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.search();
    });
    this.results = this.contentEl.createEl("div", { cls: "badger-results" });
    this.input.focus();
  }

  onClose() {
    this.searchVersion += 1;
    this.renderComponent?.unload();
    this.renderComponent = null;
  }

  /** Release Markdown renderers before replacing a result set. */
  clearResults() {
    this.renderComponent?.unload();
    this.renderComponent = null;
    this.results.empty();
  }

  /** Resolve a result name in the vault for rendering links and opening notes. */
  notePath(noteName) {
    const folder = this.plugin.settings.noteFolder.replace(/^\/+|\/+$/g, "");
    return [folder, noteName].filter(Boolean).join("/");
  }

  async search() {
    const query = this.input.value.trim();
    if (!query) return;
    const version = ++this.searchVersion;
    this.clearResults();
    this.results.createEl("p", { text: "Searching…" });
    try {
      const reply = await this.plugin.client.request("/search", "POST", {
        query,
        top_k: this.plugin.settings.topK,
      });
      if (version !== this.searchVersion) return;
      this.clearResults();
      if (!reply.results.length) {
        this.results.createEl("p", { text: "No searchable paragraphs were found." });
        return;
      }
      this.renderComponent = new Component();
      this.renderComponent.load();
      await Promise.all(reply.results.map((result) => this.renderResult(result)));
    } catch (error) {
      if (version !== this.searchVersion) return;
      this.clearResults();
      this.results.createEl("p", { text: error.message, cls: "badger-error" });
      if (error.status) this.renderChanges(error.status);
    }
  }

  renderChanges(status) {
    const list = this.results.createEl("ul");
    for (const line of changes(status)) list.createEl("li", { text: line });
    const button = this.results.createEl("button", { text: "Update index" });
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.setText("Updating…");
      try {
        await this.plugin.client.request("/index", "POST", {});
        await this.search();
      } catch (error) {
        button.disabled = false;
        button.setText("Update index");
        new Notice(error.message);
      }
    });
  }

  async renderResult(result) {
    const row = this.results.createEl("div", { cls: "badger-result" });
    const header = row.createEl("div", { cls: "badger-result-header" });
    header.createEl("span", { text: result.score.toFixed(4), cls: "badger-score" });
    const button = header.createEl("button", {
      text: result.note_name,
      cls: "badger-note-link",
    });
    button.setAttribute("aria-label", `Open ${result.note_name}`);
    button.addEventListener("click", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      return this.openNote(result, event.ctrlKey || event.metaKey);
    });
    const paragraph = row.createEl("div", { cls: "badger-paragraph" });
    await MarkdownRenderer.render(
      this.plugin.app, result.paragraph, paragraph,
      this.notePath(result.note_name), this.renderComponent,
    );
  }

  /** Use a new tab for a modified click and position its editor at the match. */
  async openNote(result, newTab = false) {
    const path = this.notePath(result.note_name);
    const file = this.plugin.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile)) {
      new Notice(`Note not found in this vault: ${path}`);
      return;
    }
    const leaf = this.plugin.app.workspace.getLeaf(newTab ? "tab" : false);
    await leaf.openFile(file);
    const view = leaf.view instanceof MarkdownView ? leaf.view : null;
    if (view?.editor) {
      const text = view.editor.getValue().replace(/\r\n/g, "\n");
      const position = text.indexOf(result.paragraph);
      if (position >= 0) view.editor.setCursor(view.editor.offsetToPos(position));
    }
    this.close();
  }
}

/** Editable connection settings, kept in Obsidian's plugin data file. */
class BadgerSettingsTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    this.containerEl.empty();
    const field = (name, description, key, placeholder) => {
      new Setting(this.containerEl)
        .setName(name)
        .setDesc(description)
        .addText((text) => text.setPlaceholder(placeholder)
          .setValue(String(this.plugin.settings[key]))
          .onChange(async (value) => {
            if (key === "topK") {
              const amount = Number(value);
              this.plugin.settings.topK = Number.isSafeInteger(amount) && amount > 0 ? amount : 10;
            } else {
              this.plugin.settings[key] = value.trim();
            }
            await this.plugin.saveData(this.plugin.settings);
          }));
    };
    field("Badger URL", "Address of 'badger serve'", "serverUrl", DEFAULTS.serverUrl);
    field("Notes folder", "Vault-relative directory matching Badger's notes_root", "noteFolder", "Zettelkasten");
    field("Top K", "Maximum number of distinct notes", "topK", "10");
    field("API token", "Only needed when the Badger service requires a token", "apiToken", "");
  }
}

/** Register inference, SHA-256 status, and indexing as separate commands. */
module.exports = class BadgerPlugin extends Plugin {
  async onload() {
    this.settings = { ...DEFAULTS, ...(await this.loadData() || {}) };
    this.client = new BadgerClient(this);
    this.addCommand({
      id: "search", name: "Search related notes",
      callback: () => new BadgerSearchModal(this).open(),
    });
    this.addCommand({
      id: "status", name: "Check note changes",
      callback: async () => {
        try {
          const reply = await this.client.request("/status");
          new BadgerStatusModal(this, "Badger: note changes", reply.status).open();
        } catch (error) {
          new Notice(error.message);
        }
      },
    });
    this.addCommand({
      id: "index", name: "Update note embeddings",
      callback: async () => {
        try {
          const reply = await this.client.request("/index", "POST", {});
          new BadgerStatusModal(
            this, "Badger: index updated", reply.before,
            `Embedded ${reply.embedded_paragraphs} paragraphs; ${reply.indexed_paragraphs} ready for search.`,
            true,
          ).open();
        } catch (error) {
          new Notice(error.message);
        }
      },
    });
    this.addCommand({
      id: "rebuild", name: "Rebuild all embeddings",
      callback: async () => {
        try {
          const reply = await this.client.request("/index", "POST", { rebuild: true });
          new BadgerStatusModal(
            this, "Badger: index rebuilt", reply.before,
            `Re-embedded ${reply.embedded_paragraphs} paragraphs.`,
            true,
          ).open();
        } catch (error) {
          new Notice(error.message);
        }
      },
    });
    this.addSettingTab(new BadgerSettingsTab(this.app, this));
  }
};
