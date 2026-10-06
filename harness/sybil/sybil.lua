-- Sybil player for the harness: dave v3.0.0-alpha.5's own Lua sybil, pointed at
-- the prt-dev-stack Anvil. Runs inside prt-harness/sybil-runner from
-- /dave/test/e2e/rollups. The harness owns the clock: this script never mines.
--
-- env: ENDPOINT, CONSENSUS, INPUT_BOX, APP, EPOCH (the sealed epoch to attack),
--      PLAYER (Anvil account index, default 2), MACHINE (template machine path),
--      MODE = fight (play every move) | join (join, then go silent) | honest (join the honest commitment, then go silent)
require "setup_path"

local Hash = require "cryptography.hash"
local Machine = require "computation.machine"
local CommitmentBuilder = require "computation.commitment"
local PatchedCommitmentBuilder = require "runners.helpers.patched_commitment"
local start_sybil = require "runners.sybil_runner"
local Reader = require "dave.reader"
local time = require "utils.time"

local ENDPOINT = assert(os.getenv("ENDPOINT"))
local EPOCH = assert(tonumber(os.getenv("EPOCH")))
local PLAYER = tonumber(os.getenv("PLAYER") or "2")
local MODE = os.getenv("MODE") or "fight"
local MACHINE = os.getenv("MACHINE") or "/machine"

local function say(fmt, ...)
    io.stdout:write(string.format("[sybil] " .. fmt .. "\n", ...))
    io.stdout:flush()
end

-- The reader derives app/consensus from factory parameters; ours are known, so set them.
local reader = setmetatable({
    input_box_address = assert(os.getenv("INPUT_BOX")),
    endpoint = ENDPOINT,
    inner_reader = nil,
    genesis = 0,
    app_address = assert(os.getenv("APP")),
    consensus_address = assert(os.getenv("CONSENSUS")),
}, { __index = Reader })

local epochs = reader:read_epochs_sealed()
local all_inputs = {}
for _, v in ipairs(reader:read_inputs_added()) do
    if string.lower(v.app_contract):sub(-40) == string.lower(reader.app_address):sub(-40) then
        table.insert(all_inputs, v.data)
    end
end
local function epoch_inputs(e)
    local out = {}
    for i = e.input_lower_bound + 1, e.input_upper_bound do table.insert(out, all_inputs[i]) end
    return out
end

-- Oracle lineage from the template, replaying chain inputs only.
os.execute("rm -rf /tmp/oracle && mkdir -p /tmp/oracle/snapshots")
local machine = Machine:new_from_path(MACHINE, "/tmp/oracle/snapshots")
for n = 0, EPOCH - 1 do
    local e = assert(epochs[n + 1], "missing sealed epoch " .. n)
    assert(Hash:from_digest_hex(e.initial_machine_state_hash) == machine:state().root_hash,
        "oracle diverges from the chain at epoch " .. n)
    machine:rollup_commitment(44, epoch_inputs(e))
end
local target = assert(epochs[EPOCH + 1], "epoch " .. EPOCH .. " is not sealed")
assert(Hash:from_digest_hex(target.initial_machine_state_hash) == machine:state().root_hash,
    "oracle diverges from the chain at the target epoch")
local snapshot = "/tmp/oracle/epoch-" .. EPOCH
machine:store_to(snapshot)
local inputs = epoch_inputs(target)
local _, honest = machine:rollup_commitment(44, inputs)
say("epoch %d: %d inputs, honest commitment %s, tournament %s", EPOCH, #inputs, honest, target.tournament)
print("HONEST " .. tostring(honest))
if os.getenv("DRYRUN") then os.exit(0) end

-- Diverge at the first level-0 leaf, as dave's multi_sybil does.
local honest_builder = CommitmentBuilder:new(snapshot, inputs, honest)
-- MODE=honest joins the honest commitment instead (a foreign first claimer, SLN-8).
local builder = honest_builder
if MODE ~= "honest" then
    builder = PatchedCommitmentBuilder:new({ { hash = Hash.zero, meta_cycle = 1 << 44 } }, honest_builder)
end
local player = start_sybil(builder, snapshot, target.tournament, inputs, PLAYER,
    { endpoint = ENDPOINT, creation_block = target.meta.block_number })

local joined = false
while true do
    local ok, log = coroutine.resume(player)
    if not ok then
        say("player error: %s", tostring(log))
        os.exit(2)
    end
    if coroutine.status(player) == "dead" then
        say("player finished")
        break
    end
    if log.has_lost then
        say("LOST")
        print("RESULT lost")
        break
    end
    if log.finished then
        say("tournament finished")
        print("RESULT finished")
        break
    end
    local count = 0
    for _ in pairs((log.state or {}).commitments or {}) do count = count + 1 end
    if count >= (MODE == "honest" and 1 or 2) and not joined then
        joined = true
        say("joined; %d commitments", count)
        print("JOINED")
        if MODE == "join" or MODE == "honest" then
            say("going silent")
            while true do time.sleep(60) end
        end
    end
    time.sleep(1)
end
