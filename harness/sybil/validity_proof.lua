-- Prints the DaveConsensus MachineValidityProof for a stored machine, as the cast tuple
-- stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[]))) takes:
-- the 32-byte data blocks holding iflags_Y, HTIF tohost and the first word of the CMIO tx buffer,
-- each with its 59 siblings bottom-up (LibBinaryMerkleTree.merkleRootAfterReplacement order).
-- A third party can stage any real machine state it holds, which is what C7/C9 need.
--
-- usage: lua5.4 validity_proof.lua <machine-dir>
local cartesi = require "cartesi"
local path = assert(arg[1], "machine path")
local m = cartesi.machine(path, { htif = { no_console_putchar = true } })
local root = m:get_root_hash()
local LOG2_LEAF = 5

local function hex(s) return "0x" .. (s:gsub(".", function(c) return string.format("%02x", c:byte()) end)) end

local function leaf_proof(addr)
    local leaf = addr & ~((1 << LOG2_LEAF) - 1)
    local block = m:read_memory(leaf, 32)
    local p = m:get_proof(leaf, LOG2_LEAF)
    local sibs = p.sibling_hashes
    -- Verify the order bottom-up; reverse when the emulator gives them top-down.
    local function fold(order)
        local node, index = cartesi.keccak256(block), leaf >> LOG2_LEAF
        for i = 1, #order do
            local s = order[i]
            if (index >> (i - 1)) & 1 == 0 then node = cartesi.keccak256(node, s) else node = cartesi.keccak256(s, node) end
        end
        return node
    end
    if fold(sibs) ~= root then
        local rev = {}
        for i = #sibs, 1, -1 do rev[#rev + 1] = sibs[i] end
        assert(fold(rev) == root, "leaf proof does not reproduce the machine root in either order")
        sibs = rev
    end
    local out = {}
    for i, s in ipairs(sibs) do out[i] = hex(s) end
    return string.format("(%s,[%s])", hex(block), table.concat(out, ","))
end

local iflags_y = m:get_reg_address("iflags_Y")
local tohost = m:get_reg_address("htif_tohost")
local tx = cartesi.AR_CMIO_TX_BUFFER_START
io.stderr:write(string.format("root %s iflags_Y@%x tohost@%x tx@%x iflags_Y=%d tohost=%x\n",
    hex(root), iflags_y, tohost, tx, m:read_reg("iflags_Y"), m:read_reg("htif_tohost")))
print(string.format("(%s,%s,%s)", leaf_proof(iflags_y), leaf_proof(tohost), leaf_proof(tx)))
