/** Small runtime check of the Obsidian adapter with mocked editor APIs. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element {
  constructor(text = "", tag = "div", cls = "") {
    this.text = text;
    this.tag = tag;
    this.cls = cls;
    this.children = [];
    this.events = {};
  }
  createEl(tag, options = {}) {
    const child = new Element(options.text || "", tag, options.cls || "");
    child.value = "";
    this.children.push(child);
    return child;
  }
  empty() { this.children = []; }
  addEventListener(name, handler) { this.events[name] = handler; }
  setAttribute() {}
  setText(value) { this.text = value; }
  focus() {}
  findText(text) {
    if (this.text.includes(text)) return this;
    for (const child of this.children) {
      const found = child.findText(text);
      if (found) return found;
    }
    return null;
  }
  findClass(cls) {
    if (this.cls === cls) return this;
    for (const child of this.children) {
      const found = child.findClass(cls);
      if (found) return found;
    }
    return null;
  }
}

class Modal {
  constructor(app) { this.app = app; this.contentEl = new Element(); }
  setTitle(title) { this.title = title; }
  open() { this.onOpen(); return this; }
  close() { this.onClose?.(); this.closed = true; }
}

class Component {
  load() { this.loaded = true; }
  unload() { this.loaded = false; }
}

class Plugin {
  constructor(app) { this.app = app; this.commands = {}; }
  async loadData() { return null; }
  async saveData() {}
  addCommand(command) { this.commands[command.id] = command; }
  addSettingTab() {}
}

class TFile {}

/** Load the actual plugin entry point with deterministic HTTP and app stubs. */
function loadPlugin(responses, opened) {
  const requests = [];
  const renderCalls = [];
  class MarkdownView {}
  const obsidian = {
    Plugin, Modal, TFile, Component,
    PluginSettingTab: class {}, Setting: class {}, MarkdownView,
    MarkdownRenderer: { render: async (_app, markdown, element, sourcePath, component) => {
      assert.equal(component.loaded, true);
      renderCalls.push({ markdown, element, sourcePath, component });
      element.text = markdown;
    } },
    Notice: class { constructor(message) { opened.notices.push(message); } },
    requestUrl: async (request) => {
      requests.push(request);
      const next = responses.shift();
      if (!next) throw new Error("No response queued");
      return { status: next.status, text: JSON.stringify(next.body) };
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "../integrations/obsidian/main.js"), "utf8");
  const module = { exports: {} };
  vm.runInNewContext(source, { module, require: () => obsidian }, { filename: "main.js" });
  const makeLeaf = (kind) => {
    const leaf = { kind, view: new MarkdownView(), file: null };
    leaf.view.editor = {
      getValue: () => "Patching divides images into small pieces.",
      offsetToPos: (offset) => ({ line: 0, ch: offset }),
      setCursor: (position) => { leaf.cursor = position; },
    };
    leaf.openFile = async (file) => { leaf.file = file; };
    return leaf;
  };
  opened.currentLeaf = makeLeaf("current");
  opened.currentLeaf.file = { path: "Current.md" };
  opened.leaves = [opened.currentLeaf];
  opened.leafKinds = [];
  const app = {
    vault: { getAbstractFileByPath: (filePath) => {
      opened.filePath = filePath;
      const file = new TFile();
      file.path = filePath;
      return file;
    } },
    workspace: {
      getLeaf: (kind) => {
        opened.leafKinds.push(kind);
        if (kind === false) return opened.currentLeaf;
        const leaf = makeLeaf(kind);
        opened.leaves.push(leaf);
        return leaf;
      },
    },
  };
  return { plugin: new module.exports(app), requests, renderCalls };
}

test("search shows the scored paragraph and opens its note", async () => {
  const opened = { notices: [] };
  const { plugin, requests, renderCalls } = loadPlugin([
    { status: 200, body: { results: [{
      score: 0.875, note_name: "nested/idea.md",
      paragraph: "Patching divides images into small pieces.",
    }] } },
  ], opened);
  await plugin.onload();
  assert.deepEqual(Object.keys(plugin.commands), ["search", "status", "index", "rebuild"]);
  const modal = plugin.commands.search.callback();
  modal.input.value = "patching";
  await modal.search();
  const header = modal.results.findClass("badger-result-header");
  assert.equal(header.children[0].text, "0.8750");
  assert.equal(header.children[1].text, "nested/idea.md");
  assert.equal(header.children.length, 2);
  assert.ok(modal.results.findText("Patching divides images into small pieces."));
  assert.equal(renderCalls[0].sourcePath, "Zettelkasten/nested/idea.md");
  assert.equal(JSON.parse(requests[0].body).query, "patching");
  await header.children[1].events.click({ button: 0, ctrlKey: false, metaKey: false, preventDefault() {} });
  assert.equal(opened.filePath, "Zettelkasten/nested/idea.md");
  assert.deepEqual(opened.leafKinds, [false]);
  assert.equal(opened.currentLeaf.file.path, "Zettelkasten/nested/idea.md");
  assert.equal(opened.currentLeaf.cursor.line, 0);
  assert.equal(modal.closed, true);
});

test("Ctrl+left click opens a new tab without replacing the current note", async () => {
  const opened = { notices: [] };
  const { plugin } = loadPlugin([
    { status: 200, body: { results: [{
      score: 0.875, note_name: "nested/idea.md",
      paragraph: "Patching divides images into small pieces.",
    }] } },
  ], opened);
  await plugin.onload();
  const modal = plugin.commands.search.callback();
  modal.input.value = "patching";
  await modal.search();
  const button = modal.results.findClass("badger-note-link");
  await button.events.click({ button: 0, ctrlKey: true, metaKey: false, preventDefault() {} });
  assert.deepEqual(opened.leafKinds, ["tab"]);
  assert.equal(opened.currentLeaf.file.path, "Current.md");
  assert.equal(opened.leaves[1].file.path, "Zettelkasten/nested/idea.md");
  assert.equal(opened.leaves[1].cursor.line, 0);
  assert.equal(modal.closed, true);
});

test("inline and display math are handed to Obsidian's Markdown renderer", async () => {
  const paragraph = "Forecast $y_t$ using\n$$\n\\hat{y}_{t+k}=f(y_{1:t})\n$$";
  const { plugin, renderCalls } = loadPlugin([
    { status: 200, body: { results: [{
      score: 0.6637, note_name: "MQ-RNN.md", paragraph,
    }] } },
  ], { notices: [] });
  await plugin.onload();
  const modal = plugin.commands.search.callback();
  modal.input.value = "probabilistic forecasting";
  await modal.search();
  assert.equal(renderCalls.length, 1);
  assert.equal(renderCalls[0].markdown, paragraph);
  assert.equal(renderCalls[0].element.cls, "badger-paragraph");
  assert.equal(renderCalls[0].sourcePath, "Zettelkasten/MQ-RNN.md");
  const component = renderCalls[0].component;
  modal.close();
  assert.equal(component.loaded, false);
});

test("stale search lists the changed note and updates only after a click", async () => {
  const opened = { notices: [] };
  const { plugin, requests } = loadPlugin([
    { status: 409, body: { error: "Notes changed", status: {
      new: [], changed: ["idea.md"], removed: [], rebuild: false, needs_update: true,
    } } },
    { status: 200, body: { embedded_paragraphs: 1, indexed_paragraphs: 1 } },
    { status: 200, body: { results: [] } },
  ], opened);
  await plugin.onload();
  const modal = plugin.commands.search.callback();
  modal.input.value = "patching";
  await modal.search();
  assert.ok(modal.results.findText("Changed: idea.md"));
  assert.equal(requests.length, 1);
  await modal.results.findText("Update index").events.click();
  assert.equal(requests.length, 3);
  assert.ok(requests[1].url.endsWith("/index"));
  assert.ok(modal.results.findText("No searchable paragraphs"));
});
