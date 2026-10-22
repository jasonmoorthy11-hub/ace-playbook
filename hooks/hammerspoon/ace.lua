-- ACE brief on wake (hooks/INSTALL.md section 8). Load from ~/.hammerspoon/init.lua with:
--   dofile(os.getenv("HOME") .. "/ace-playbook/hooks/hammerspoon/ace.lua")
-- Runs hooks/brief.sh when the Mac wakes (20 s later) or the screen unlocks. brief.sh dedupes (once per brief_dedupe_hours), holds its own
-- lock and notifies only when something needs you, so firing on both events is safe. ~/.hammerspoon/ace_config.lua returns a table
-- { brief_script = "/absolute/path/to/brief.sh", config_file = "/absolute/path/to/config.json" } (brief_script is optional). config_file is
-- REQUIRED: it is exported to the task as ACE_PLAYBOOK_CONFIG and MUST be the same file as __ACE_CONFIG_FILE__ in the LaunchAgent, because the
-- brief dir, the lock and the exclusions all come from that file. A value counts only when it is a string with a non-blank character:
-- "" (or blanks) is the same as no value, and brief_script then keeps its default. The run is SKIPPED, never run on the default config (that
-- has another dir and lock and no exclusions), with one printed line at load and a printed line plus an alert at each wake, when config_file
-- is missing or empty, when ace_config.lua fails to load (a missing file is not logged: it is just no config), when it returns something
-- other than a table, or when brief_script or config_file is present and not a string. Nothing here raises: init.lua keeps loading.
-- The task's environment is HOME, USER, LOGNAME, PATH and ACE_PLAYBOOK_CONFIG: the claude CLI needs USER to find its login (without it a wake
-- run is "Not logged in"). An unset USER or LOGNAME is simply left out.
local home = os.getenv("HOME")
local script = os.getenv("HOME") .. "/ace-playbook/hooks/brief.sh"
local config_file = nil
local malformed = false      -- set by any unusable ace_config value: the run is skipped (fail closed)
local function usable(v) return type(v) == "string" and v:match("%S") ~= nil end
local ok, local_config = pcall(require, "ace_config")
if not ok then
  if not tostring(local_config):find("module 'ace_config' not found", 1, true) then
    malformed = true
    hs.printf("ace brief: ace_config.lua did not load, the brief run is skipped (hooks/INSTALL.md section 8)")
  end
elseif type(local_config) ~= "table" then
  malformed = true
  hs.printf("ace brief: ace_config.lua must return a table, the brief run is skipped (hooks/INSTALL.md section 8)")
else
  if local_config.brief_script ~= nil and type(local_config.brief_script) ~= "string" then
    malformed = true
    hs.printf("ace brief: brief_script in ace_config.lua is not a string, the brief run is skipped (hooks/INSTALL.md section 8)")
  elseif usable(local_config.brief_script) then script = local_config.brief_script end
  if local_config.config_file ~= nil and type(local_config.config_file) ~= "string" then
    malformed = true
    hs.printf("ace brief: config_file in ace_config.lua is not a string, the brief run is skipped (hooks/INSTALL.md section 8)")
  elseif usable(local_config.config_file) then config_file = local_config.config_file end
end

-- The task and the timer live in module-level (global) variables: Hammerspoon collects an hs.task or hs.timer nothing refers to, which
-- would silently kill a run or cancel the 20 s delay.
ace_brief_task = nil
ace_brief_timer = nil

local function run_brief()
  if ace_brief_task and ace_brief_task:isRunning() then return end
  if malformed or not config_file then      -- fail closed: no silent fallback to the default config
    hs.printf("ace brief: no usable config_file in ~/.hammerspoon/ace_config.lua, the brief run is skipped (hooks/INSTALL.md section 8)")
    hs.alert.show("ACE brief skipped: set config_file in ace_config.lua")
    return
  end
  ace_brief_task = hs.task.new("/bin/bash", function(code, _out, err)
    if code ~= 0 then print("ace brief: exit " .. tostring(code) .. " " .. tostring(err)) end
  end, { script })
  local env = { HOME = home, PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin:" .. home .. "/.local/bin",
                USER = os.getenv("USER"), LOGNAME = os.getenv("LOGNAME") or os.getenv("USER"), ACE_PLAYBOOK_CONFIG = config_file }
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
