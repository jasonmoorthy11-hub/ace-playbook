-- ACE brief on wake (hooks/INSTALL.md section 8). Load from ~/.hammerspoon/init.lua with:
--   dofile(os.getenv("HOME") .. "/ace-playbook/hooks/hammerspoon/ace.lua")
-- Runs hooks/brief.sh when the Mac wakes or the screen unlocks. brief.sh dedupes (once per brief_dedupe_hours), holds its own lock and
-- notifies only when something needs you, so firing on both events is safe. ~/.hammerspoon/ace_config.lua returns
-- { brief_script = "/absolute/path/to/brief.sh", config_file = "/absolute/path/to/config.json" } (brief_script is optional). config_file is
-- REQUIRED: it is exported to the task as ACE_PLAYBOOK_CONFIG and MUST be the same file as __ACE_CONFIG_FILE__ in the LaunchAgent, because the
-- brief dir, the lock and the exclusions all come from that file. Without it a wake run would read the default config (another dir and
-- lock, no exclusions), so the run is skipped with a warning instead.
local home = os.getenv("HOME")
local script = os.getenv("HOME") .. "/ace-playbook/hooks/brief.sh"
local config_file = nil
local ok, local_config = pcall(require, "ace_config")
if ok and type(local_config) == "table" then
  if type(local_config.brief_script) == "string" then script = local_config.brief_script end
  if type(local_config.config_file) == "string" then config_file = local_config.config_file end
end

-- The task and the timer live in module-level (global) variables: Hammerspoon collects an hs.task or hs.timer nothing refers to, which
-- would silently kill a run or cancel the 20 s delay.
ace_brief_task = nil
ace_brief_timer = nil

local function run_brief()
  if ace_brief_task and ace_brief_task:isRunning() then return end
  if not config_file then      -- fail closed: no silent fallback to the default config
    hs.printf("ace brief: no config_file in ~/.hammerspoon/ace_config.lua, the brief run is skipped (hooks/INSTALL.md section 8)")
    hs.alert.show("ACE brief skipped: set config_file in ace_config.lua")
    return
  end
  ace_brief_task = hs.task.new("/bin/bash", function(code, _out, err)
    if code ~= 0 then print("ace brief: exit " .. tostring(code) .. " " .. tostring(err)) end
  end, { script })
  local env = { HOME = home, PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin:" .. home .. "/.local/bin",
                ACE_PLAYBOOK_CONFIG = config_file }
  ace_brief_task:setEnvironment(env)
  ace_brief_task:start()
end

-- the network is not up the instant the lid opens
local function soon()
  if ace_brief_timer then ace_brief_timer:stop() end
  ace_brief_timer = hs.timer.doAfter(20, run_brief)
end

ace_brief_watcher = hs.caffeinate.watcher.new(function(event)
  if event == hs.caffeinate.watcher.systemDidWake or event == hs.caffeinate.watcher.screensDidUnlock then soon() end
end)
ace_brief_watcher:start()
