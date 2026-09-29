# Badger the Ragger

```space-lua
-- Edit these four values for your SilverBullet server and space.
badger = badger or {}
badger.serverUrl = "http://127.0.0.1:8765"
badger.apiToken = ""
badger.notePrefix = "Zettelkasten" -- Relative to the SilverBullet space; use "" for its root.
badger.topK = 10
badger.results = badger.results or nil
badger.status = badger.status or nil
badger.message = badger.message or "Search your notes by concept."
badger.indexedJustNow = false

-- Request the shared Python service from the SilverBullet server's network.
function badger.request(path, method, body)
  local headers = { ["Content-Type"] = "application/json" }
  if badger.apiToken ~= "" then
    headers["Authorization"] = "Bearer " .. badger.apiToken
  end
  local options = { method = method, headers = headers }
  if body ~= nil then options.body = body end
  return net.proxyFetch(badger.serverUrl .. path, options)
end

-- Convert a note path relative to notes_root into a SilverBullet page name.
function badger.pageName(noteName)
  local page = noteName:gsub("%.md$", "")
  if badger.notePrefix ~= "" then
    page = badger.notePrefix:gsub("/$", "") .. "/" .. page
  end
  return page
end

-- Render every affected path and each complete score; note; paragraph record.
function badger.render()
  local lines = { "# Badger", badger.message }
  if badger.status then
    local status = badger.status
    if status.rebuild then
      lines[#lines + 1] = badger.indexedJustNow and "A full index build was performed."
        or "A full index build is needed."
    end
    for _, group in ipairs({ { "New", status.new or {} },
                             { "Changed", status.changed or {} },
                             { "Removed", status.removed or {} } }) do
      for _, name in ipairs(group[2]) do
        lines[#lines + 1] = "- " .. group[1] .. ": `" .. name .. "`"
      end
    end
    if not status.needs_update then lines[#lines + 1] = "All indexed notes are current." end
  end
  if badger.results then
    if #badger.results == 0 then lines[#lines + 1] = "No searchable paragraphs were found." end
    for _, result in ipairs(badger.results) do
      -- Escaping internal line breaks keeps the required record on one line.
      local paragraph = result.paragraph:gsub("\\", "\\\\"):gsub("\n", "\\n")
      local record = string.format("%.4f; %s; %s", result.score, result.note_name, paragraph)
      local fence = "```"
      while record:find(fence, 1, true) do fence = fence .. "`" end
      lines[#lines + 1] = fence .. "text\n" .. record .. "\n" .. fence
        .. "\n\n[[" .. badger.pageName(result.note_name) .. "|Open note]]"
    end
  end
  return table.concat(lines, "\n\n")
end

view.define {
  name = "badger.results",
  title = "Badger results",
  dock = "rhs",
  refreshOnOpen = true,
  view = view.new { content = function() return badger.render() end },
}

-- Search never embeds notes implicitly; a stale response shows file names.
command.define {
  name = "Badger: Search",
  run = function()
    local query = editor.prompt("Search notes for an idea or concept")
    if not query or query == "" then return end
    local reply = badger.request("/search", "POST", { query = query, top_k = badger.topK })
    if reply.ok then
      badger.results = reply.body.results or {}
      badger.status = nil
      badger.indexedJustNow = false
      badger.message = "Results for: " .. query
    else
      badger.results = nil
      badger.status = reply.body and reply.body.status or nil
      badger.indexedJustNow = false
      badger.message = reply.body and reply.body.error or "Badger request failed."
    end
    view.open("badger.results")
  end,
}

-- Explicitly report SHA-256 changes without loading the embedding model.
command.define {
  name = "Badger: Check note changes",
  run = function()
    local reply = badger.request("/status", "GET")
    badger.results = nil
    badger.status = reply.body and reply.body.status or nil
    badger.indexedJustNow = false
    badger.message = reply.ok and "Note index status" or "Could not check note status."
    view.open("badger.results")
  end,
}

-- Embed only new or changed notes and remove deleted ones.
command.define {
  name = "Badger: Update note embeddings",
  run = function()
    local reply = badger.request("/index", "POST", {})
    badger.results = nil
    if reply.ok then
      badger.status = reply.body.before
      badger.indexedJustNow = true
      badger.message = "Embedded " .. reply.body.embedded_paragraphs
        .. " paragraphs; " .. reply.body.indexed_paragraphs .. " available for search."
    else
      badger.status = nil
      badger.indexedJustNow = false
      badger.message = reply.body and reply.body.error or "Index update failed."
    end
    view.open("badger.results")
  end,
}

-- Re-embed the entire corpus when the model behind the same name changes.
command.define {
  name = "Badger: Rebuild all embeddings",
  run = function()
    local reply = badger.request("/index", "POST", { rebuild = true })
    badger.results = nil
    badger.status = reply.ok and reply.body.before or nil
    badger.indexedJustNow = reply.ok
    badger.message = reply.ok and ("Rebuilt " .. reply.body.indexed_paragraphs .. " paragraphs.")
      or (reply.body and reply.body.error or "Index rebuild failed.")
    view.open("badger.results")
  end,
}
```
