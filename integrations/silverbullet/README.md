# SilverBullet 2 integration

Run `badger serve` on the machine that can read the notes. Add `Badger.md` to
your SilverBullet space. Edit the four values at the top of its `space-lua`
block: service URL, optional token, note prefix, and default Top K.

The `notePrefix` is the path from the SilverBullet space root to Badger's
`notes_root`. For a space with `Zettelkasten/idea.md`, set `notes_root` to that
`Zettelkasten` directory and keep `notePrefix = "Zettelkasten"`. For a space
whose root *is* `notes_root`, use `notePrefix = ""`. In the latter case, add
`exclude_globs = ["Badger.md", "CONFIG.md", "Library/**"]` to `badger.toml`
so configuration pages and this script are not embedded as ordinary notes.

Only notes with `Note Status: Completed` in their leading metadata or YAML
front matter are indexed. Editing a `Note Status: #in-progress` page leaves
the index current; completing it makes it eligible for **Badger: Update note
embeddings**. Later changes to a Completed page re-embed that page alone.
After upgrading, restart `badger serve` and update the index once to remove
previously indexed in-progress pages without re-embedding unchanged pages.

Open the SilverBullet command palette to run **Badger: Search**, **Badger:
Check note changes**, **Badger: Update note embeddings**, or **Badger: Rebuild
all embeddings**. Results appear in a side panel as complete one-line
`score; note name; paragraph` records. Each result has an **Open note** link.
The panel shows all changed file names when a search finds a stale index.

`net.proxyFetch` connects from the SilverBullet **server**, even if you are
using SilverBullet through a browser elsewhere. If that server runs in Docker,
`127.0.0.1` means the container itself; make the Badger service reachable from
the container and change `serverUrl` accordingly. For a non-local network
binding, set `BADGER_API_TOKEN` before starting Badger and paste the same token
into `apiToken`. SilverBullet requires write access for its HTTP proxy. Only
run this script in a space whose editors you trust.
