-- ACE brief on wake (hooks/INSTALL.md section 8). Load from ~/.hammerspoon/init.lua with:
--   dofile(os.getenv("HOME") .. "/ace-playbook/hooks/hammerspoon/ace.lua")
-- Runs hooks/brief.sh when the Mac wakes or the screen unlocks. brief.sh dedupes (once per brief_dedupe_hours), holds its own lock and
-- notifies only when something needs you, so firing on both events is safe. Optional override: ~/.hammerspoon/ace_config.lua returning
-- { brief_script = "/absolute/path/to/brief.sh" }.
local home = os.getenv("HOME")
local script = os.getenv("HOME") .. "/ace-playbook/hooks/brief.sh"
local ok, local_config = pcall(require, "ace_config")
if ok and type(local_config) == "table" and type(local_config.brief_script) == "string" then
  script = local_config.brief_script
end

local running = false

local function run_brief()
  if running then return end
  running = true
  local task = hs.task.new("/bin/bash", function(code, _out, err)
    running = false
    if code ~= 0 then print("ace brief: exit " .. tostring(code) .. " " .. tostring(err)) end
  end, { script })
  task:setEnvironment({ HOME = home, PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin:" .. home .. "/.local/bin" })
  if not task:start() then running = false end
end

-- the network is not up the instant the lid opens
local function soon() hs.timer.doAfter(20, run_brief) end

ace_brief_watcher = hs.caffeinate.watcher.new(function(event)
  if event == hs.caffeinate.watcher.systemDidWake or event == hs.caffeinate.watcher.screensDidUnlock then soon() end
end)
ace_brief_watcher:start()
