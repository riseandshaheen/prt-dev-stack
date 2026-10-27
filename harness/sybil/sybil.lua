-- Sybil player for the harness: dave v3.0.0-alpha.6's own Lua PRT client, pointed at the
-- prt-dev-stack Anvil. Runs inside prt-harness/sybil-runner:3.0.0-alpha.6 from
-- /dave/test/e2e/rollups. The harness owns the clock: this script never mines.
--
-- env: ENDPOINT, CONSENSUS, INPUT_BOX, APP, EPOCH (the sealed epoch to attack),
--      PLAYER (Anvil account index, default 2; signs with that account's dev key),
--      MACHINE (template machine path, default /machine),
--      MODE = fight (play every move) | join (join, then go silent)
--           | honest (join the honest commitment, then go silent: a foreign first claimer)
--           | honest-fight (defend the honest commitment with the Lua client: a stand-in node),
--      TRANSITION (decimal, optional): the transition the dispute is steered onto, with one
--        patch per level of the deployed table (dave's Env.steering_patches). Default is dave's
--        smoke divergence, meta-cycle 2^44 (transition 2^44 - 1, the ustep+reset closing big cycle
--        2^24 of input 0, which with a short input is a fixed-point step).
--      WRONG_FINAL=1: also patch the last leaf, so the claimed post-epoch state is the epoch's
--        initial state (a real machine state the sybil can prove) instead of the honest one.
--      PATCH_SALT (string, optional): patched leaves carry keccak(salt) instead of Hash.zero, so two
--        sybils steered onto nearby transitions still have distinct roots (C2).
--      TOURNAMENT (address, optional), CREATION_BLOCK and ROOT_LEVEL: play in that tournament instead of the
--        epoch's root, e.g. a child tournament whose contested class the sybil copies (C2).
--      TICK (seconds between reactions, default 1), DRYRUN=1 (compute commitments and exit).
--
-- Markers on stdout, one per line, for sybil.py: HONEST <root>, SYBIL <root>, TOURNAMENT <addr>,
-- JOINED, RESULT lost|finished|dead, ACT <intent>.
require "setup_path"

local Hash = require "cryptography.hash"
local Machine = require "computation.machine"
local CommitmentBuilder = require "computation.commitment"
local PatchedCommitmentBuilder = require "runners.helpers.patched_commitment"
local Actor = require "player.actor"
local SemanticReader = require "player.semantic_reader"
local Sender = require "player.sender"
local Reader = require "dave.reader"
local constants = require "blockchain.constants"
local time = require "utils.time"
local uint256 = require "utils.bint" (256)

local ENDPOINT = assert(os.getenv("ENDPOINT"))
local EPOCH = assert(tonumber(os.getenv("EPOCH")))
local PLAYER = tonumber(os.getenv("PLAYER") or "2")
local MODE = os.getenv("MODE") or "fight"
local MACHINE = os.getenv("MACHINE") or "/machine"
local TICK = tonumber(os.getenv("TICK") or "1")
local TRANSITION = os.getenv("TRANSITION")
local WRONG_FINAL = os.getenv("WRONG_FINAL") == "1"
local PATCH_HASH = os.getenv("PATCH_SALT") and Hash:from_data(os.getenv("PATCH_SALT")) or Hash.zero
local TOURNAMENT = os.getenv("TOURNAMENT")
local CREATION_BLOCK = tonumber(os.getenv("CREATION_BLOCK") or "0")

local function say(fmt, ...)
    io.stdout:write(string.format("[sybil] " .. fmt .. "\n", ...))
    io.stdout:flush()
end
local function mark(fmt, ...)
    io.stdout:write(string.format(fmt .. "\n", ...))
    io.stdout:flush()
end

-- The reader derives app/consensus from factory parameters; ours are known, so set them.
local reader = setmetatable({
    input_box_address = assert(os.getenv("INPUT_BOX")),
    endpoint = ENDPOINT,
    app_address = assert(os.getenv("APP")),
    consensus_address = assert(os.getenv("CONSENSUS")),
}, { __index = Reader })

-- The deployed table, top first: alpha.6 devnet is [{38, 54}, {0, 38}].
local levels = reader:read_tournament_levels()
local root_stride = levels[1].log2_stride
say("tournament levels: %s", table.concat((function()
    local t = {}
    for _, l in ipairs(levels) do t[#t + 1] = string.format("(stride %d, height %d)", l.log2_stride, l.height) end
    return t
end)(), " "))

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
    machine:rollup_commitment(root_stride, epoch_inputs(e))
end
local target = assert(epochs[EPOCH + 1], "epoch " .. EPOCH .. " is not sealed")
local initial_state = machine:state().root_hash
assert(Hash:from_digest_hex(target.initial_machine_state_hash) == initial_state,
    "oracle diverges from the chain at the target epoch")
local snapshot = "/tmp/oracle/epoch-" .. EPOCH
machine:store_to(snapshot)
local inputs = epoch_inputs(target)
local _, honest, processing_bigs = machine:rollup_commitment(root_stride, inputs)
for i, bigs in pairs(processing_bigs) do say("input %d consumed %s big cycles", i - 1, tostring(bigs)) end
say("epoch %d: %d inputs, honest commitment %s (final %s), tournament %s at block %d",
    EPOCH, #inputs, honest, honest:last(), target.tournament, target.meta.block_number)
mark("HONEST %s", tostring(honest))
mark("TOURNAMENT %s", target.tournament)

-- Patches that steer the dispute onto `transition` (dave's Env.steering_patches): the leaf level
-- gets the post-state meta-cycle itself, every strided level the leaf enclosing it.
local function steering_patches(transition)
    local tgt = transition + 1
    local metas = { [tostring(tgt)] = tgt }
    for _, level in ipairs(levels) do
        -- EXACT_ONLY=1 skips the strided levels' rounded patches, so a child-level player keeps the
        -- child's last leaf (its contested class) honest and garbles only the steered leaf (C2).
        if level.log2_stride > 0 and os.getenv("EXACT_ONLY") ~= "1" then
            local rounded = ((tgt + (uint256.one() << level.log2_stride) - 1) >> level.log2_stride) << level.log2_stride
            metas[tostring(rounded)] = rounded
        end
    end
    local patches = {}
    for _, meta in pairs(metas) do table.insert(patches, { hash = PATCH_HASH, meta_cycle = meta }) end
    return patches
end

local honest_builder = CommitmentBuilder:new(snapshot, inputs, honest)
local builder = honest_builder
if MODE ~= "honest" and MODE ~= "honest-fight" then
    local transition = TRANSITION and uint256.parse(TRANSITION) or ((uint256.one() << 44) - 1)
    local patches = steering_patches(transition)
    if WRONG_FINAL then
        -- The last leaf of the epoch ruler: position 2^54 - 1 at the root, 2^38 - 1 in its last child.
        local ruler_span = 0
        for _, l in ipairs(levels) do ruler_span = ruler_span + l.height end
        table.insert(patches, { hash = initial_state, meta_cycle = uint256.one() << ruler_span })
    end
    say("steering onto transition %s with %d patches%s", tostring(transition), #patches,
        WRONG_FINAL and ", claiming the initial state as final" or "")
    builder = PatchedCommitmentBuilder:new(patches, honest_builder)
    -- The sybil's root commitment, for the markers (the builder caches it).
    local root = builder:build(uint256.zero(), 0, levels[1].log2_stride, levels[1].height)
    mark("SYBIL %s", tostring(root))
else
    mark("SYBIL %s", tostring(honest))
end
if os.getenv("DRYRUN") then os.exit(0) end

-- The Lua fold roots at level 0; playing inside a child needs its real level (ROOT_LEVEL).
if os.getenv("ROOT_LEVEL") then
    local Fold = require "player.fold"
    local orig_new = Fold.new
    Fold.new = function(root, options)
        local f = orig_new(root, options)
        f._tournaments[root].level = tonumber(os.getenv("ROOT_LEVEL"))
        return f
    end
end

-- A child snapshot needs its parent link (the root, the match that created the child and the side
-- this player copies): PARENT_TOURNAMENT, PARENT_ONE, PARENT_TWO, PARENT_COMMITMENT.
if os.getenv("PARENT_TOURNAMENT") then
    local Domain = require "player.domain"
    local Adapter = require "player.adapter"
    local parent = Domain.parent_link(
        Adapter.normalize_address(os.getenv("PARENT_TOURNAMENT"), "parent tournament"),
        Domain.match_id(Hash:from_digest_hex(os.getenv("PARENT_ONE")), Hash:from_digest_hex(os.getenv("PARENT_TWO"))),
        Hash:from_digest_hex(os.getenv("PARENT_COMMITMENT")))
    local orig_snapshot = Domain.snapshot
    Domain.snapshot = function(args)
        if args.parent == nil and args.descriptor and args.descriptor.level ~= 0 then args.parent = parent end
        return orig_snapshot(args)
    end
end

local pk = assert(constants.pks[PLAYER + 1], "no dev key for Anvil account " .. PLAYER)
local actor = Actor.new {
    reader = SemanticReader.from_endpoint(TOURNAMENT or target.tournament,
        TOURNAMENT and CREATION_BLOCK or target.meta.block_number, ENDPOINT),
    commitment_builder = builder,
    machine_path = snapshot,
    inputs = inputs,
    sender = Sender:new(pk, PLAYER, ENDPOINT),
    -- Patched sybils deliberately claim a wrong post-state and still submit the locally valid
    -- proof, so the contract rejects the move and the adversarial clock path is exercised.
    allow_invalid_claims = MODE ~= "honest-fight",
}

local joined = false
local silent = MODE == "join" or MODE == "honest"
while true do
    local ok, log = pcall(actor.react, actor)
    if not ok then
        say("player error: %s", tostring(log))
        mark("RESULT dead")
        os.exit(2)
    end
    local d = log.decision
    if d and d._tag then
        local intent = d.intent and d.intent._tag
        if intent then mark("ACT %s", tostring(intent)) end
    end
    if log.has_lost then
        say("LOST")
        mark("RESULT lost")
        break
    end
    if log.finished then
        say("tournament finished")
        mark("RESULT finished")
        break
    end
    local mine = builder:build(uint256.zero(), 0, levels[1].log2_stride, levels[1].height)
    local present = false
    for k in pairs(log.state and log.state.commitments or {}) do
        if tostring(k) == tostring(mine) then present = true end
    end
    if not joined and present then
        joined = true
        local count = 0
        for _ in pairs(log.state.commitments) do count = count + 1 end
        say("joined; %d commitments", count)
        mark("JOINED")
        if silent then
            say("going silent")
            while true do time.sleep(60) end
        end
    end
    time.sleep(TICK)
end
