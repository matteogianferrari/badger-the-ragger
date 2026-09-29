# Obsidian desktop integration

Copy `manifest.json`, `main.js`, and `styles.css` into
`<your vault>/.obsidian/plugins/badger-the-ragger/`. Enable **Badger the Ragger**
in Community plugins. Start `badger serve` separately before using it.
If you already have the plugin installed, replace those three files and reload
the plugin in Obsidian.

In the plugin settings, set **Notes folder** to the directory inside the
Obsidian vault that corresponds to `notes_root` in `badger.toml`. The default
is `Zettelkasten`; use an empty setting if `notes_root` is the vault root.

The command palette offers **Search related notes**, **Check note changes**,
**Update note embeddings**, and **Rebuild all embeddings**. Each search result
shows its similarity score beside a clickable note name, without semicolons in
the header. The matching paragraph is rendered as Obsidian Markdown, including
inline `$...$` and display `$$...$$` math. Click the note name to open it;
Badger moves the cursor to the first exact occurrence of that paragraph when
it can find one. A stale index lists all affected files and offers an explicit
**Update index** button.

Similarity scores appear in bold `#ffe14d`. A normal click opens the note in
the current tab; **Ctrl+left click** opens it in a new tab without replacing
the current note. On macOS, Command+left click does the same.

The plugin sends requests only to the configured Badger URL. It stores the
optional API token in Obsidian's plugin settings. This integration targets the
desktop app with a local Python service; it does not run the model in Obsidian.
