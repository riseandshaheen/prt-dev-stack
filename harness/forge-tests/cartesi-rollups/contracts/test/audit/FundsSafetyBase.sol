// SPDX-License-Identifier: Apache-2.0
// Shared fixture for the funds-safety forge cases (prt-dev-stack/test-plan-funds-safety.md).
//
// Real DaveAppFactory -> DaveConsensus + Application (rollups-contracts 3.0.0-alpha.10) from
// dave v3.0.0-alpha.6. Only the tournament *outcome* is stubbed: a contract answering
// tournamentStanding(), which is the single trust input DaveConsensus reads when staging.
// Machine states (outputs tree, accounts drive, iflags.Y, HTIF) are built with the
// rollups-contracts LibEmulator so every Merkle proof is real.
pragma solidity ^0.8.30;

import {Vm} from "forge-std-1.9.6/src/Vm.sol";

import {AccountValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/AccountValidityProof.sol";
import {MachineValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/MachineValidityProof.sol";
import {OutputValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/OutputValidityProof.sol";
import {Outputs} from "cartesi-rollups-contracts-3.0.0/src/common/Outputs.sol";
import {WithdrawalConfig} from "cartesi-rollups-contracts-3.0.0/src/common/WithdrawalConfig.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {IInputBox} from "cartesi-rollups-contracts-3.0.0/src/inputs/IInputBox.sol";
import {IWithdrawalOutputBuilder} from "cartesi-rollups-contracts-3.0.0/src/withdrawal/IWithdrawalOutputBuilder.sol";
import {ConsensusTestUtils} from "cartesi-rollups-contracts-3.0.0/test/util/ConsensusTestUtils.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";

import {IDataProvider} from "prt-contracts/IDataProvider.sol";
import {ITournament} from "prt-contracts/ITournament.sol";
import {ITournamentFactory} from "prt-contracts/ITournamentFactory.sol";
import {Machine} from "prt-contracts/types/Machine.sol";
import {Tree} from "prt-contracts/types/Tree.sol";

import {DaveAppFactory} from "src/DaveAppFactory.sol";
import {IDaveConsensus} from "src/IDaveConsensus.sol";

// ---- minimal tournament-result oracle (only tournamentStanding() is read by DaveConsensus) ----
contract StubTournament {
    ITournament.TournamentStanding internal _standing;
    Machine.Hash internal _final;

    function setWinner(Machine.Hash h) external {
        _standing = ITournament.TournamentStanding.ROOT_WINNER;
        _final = h;
    }

    function setFailed() external {
        _standing = ITournament.TournamentStanding.ROOT_FAILED;
        _final = Machine.ZERO_STATE;
    }

    function tournamentStanding() external view returns (ITournament.TournamentStandingView memory v) {
        v.standing = _standing;
        v.hasCandidate = _standing == ITournament.TournamentStanding.ROOT_WINNER;
        v.candidate = Tree.Node.wrap(keccak256("candidate"));
        v.finalState = _final;
    }
}

contract StubTournamentFactory is ITournamentFactory {
    StubTournament public last;
    uint256 public count;

    function instantiate(Machine.Hash, IDataProvider) external override returns (ITournament) {
        last = new StubTournament();
        ++count;
        return ITournament(address(last));
    }
}

abstract contract FundsSafetyBase is ConsensusTestUtils {
    using LibEmulator for LibEmulator.State;
    using LibEmulator for LibEmulator.ProofComponents;

    struct App {
        IApplication app;
        IDaveConsensus dc;
        StubTournamentFactory tf;
        DaveAppFactory factory;
    }

    LibEmulator.State internal _emu;

    address internal constant GUARDIAN = address(0xBEEF);
    address internal constant SENTRY_MANAGER = address(0xA11CE);
    bytes32 internal constant TEMPLATE = keccak256("tmpl");

    // ---- deployment ----

    function _wc() internal view returns (WithdrawalConfig memory) {
        return _wcWith(GUARDIAN, _contracts.dev.testUsdWithdrawalOutputBuilder);
    }

    function _wcWith(address guardian, IWithdrawalOutputBuilder builder)
        internal
        pure
        returns (WithdrawalConfig memory)
    {
        return WithdrawalConfig({
            guardian: guardian,
            log2LeavesPerAccount: LibEmulator.LOG2_LEAVES_PER_ACCOUNT,
            log2MaxNumOfAccounts: LibEmulator.LOG2_MAX_NUM_OF_ACCOUNTS,
            accountsDriveStartIndex: LibEmulator.ACCOUNTS_DRIVE_START_INDEX,
            withdrawalOutputBuilder: builder
        });
    }

    function _zeroWc() internal pure returns (WithdrawalConfig memory) {
        return WithdrawalConfig({
            guardian: address(0),
            log2LeavesPerAccount: 0,
            log2MaxNumOfAccounts: 0,
            accountsDriveStartIndex: 0,
            withdrawalOutputBuilder: IWithdrawalOutputBuilder(address(0))
        });
    }

    function _noSentries() internal pure returns (address[] memory s) {
        s = new address[](0);
    }

    function _deploy(uint256 stagingPeriod) internal returns (App memory a) {
        return _deploy(stagingPeriod, _noSentries(), _wc(), bytes32(uint256(0x5a17)));
    }

    function _deploy(uint256 stagingPeriod, address[] memory sentries) internal returns (App memory a) {
        return _deploy(stagingPeriod, sentries, _wc(), bytes32(uint256(0x5a17)));
    }

    function _deploy(uint256 stagingPeriod, address[] memory sentries, WithdrawalConfig memory wc, bytes32 salt)
        internal
        returns (App memory a)
    {
        a.tf = new StubTournamentFactory();
        a.factory = new DaveAppFactory(_contracts.core.inputBox, _contracts.core.applicationFactory, a.tf);
        (a.app, a.dc) = a.factory.newDaveApp(TEMPLATE, stagingPeriod, SENTRY_MANAGER, sentries, wc, salt);
    }

    // ---- machine state construction ----

    /// @dev A fresh settled machine (RX_ACCEPTED manual yield, iflags.Y set) with no outputs/accounts.
    function _resetMachine() internal {
        delete _emu;
        _emu.setIflagsY(1);
        _emu.setHtifTohostRxAccepted();
    }

    function _addOutput(bytes memory output) internal returns (uint64) {
        return LibEmulator.OutputIndex.unwrap(_emu.addOutput(output));
    }

    function _addAccount(bytes memory account) internal returns (uint64) {
        return LibEmulator.AccountIndex.unwrap(_emu.addAccount(account));
    }

    function _pc() internal view returns (LibEmulator.ProofComponents memory) {
        return _emu.buildProofComponents();
    }

    function _outputProof(uint64 idx) internal view returns (OutputValidityProof memory) {
        return _emu.getOutputValidityProof(LibEmulator.OutputIndex.wrap(idx));
    }

    function _accountProof(uint64 idx) internal view returns (AccountValidityProof memory) {
        return _emu.getAccountValidityProof(LibEmulator.AccountIndex.wrap(idx));
    }

    function _accountsDriveRoot() internal view returns (bytes32) {
        return _emu.getAccountsDriveMerkleRoot();
    }

    function _voucher(address dest, uint256 value, bytes memory payload) internal pure returns (bytes memory) {
        return abi.encodeCall(Outputs.Voucher, (dest, value, payload));
    }

    function _delegateCallVoucher(address dest, bytes memory payload) internal pure returns (bytes memory) {
        return abi.encodeCall(Outputs.DelegateCallVoucher, (dest, payload));
    }

    function _notice(bytes memory payload) internal pure returns (bytes memory) {
        return abi.encodeCall(Outputs.Notice, (payload));
    }

    // ---- settlement ----

    function _epoch(App memory a) internal view returns (uint256 e) {
        (e,,,,,,,) = a.dc.getCurrentSealedEpoch();
    }

    function _stage(App memory a, LibEmulator.ProofComponents memory pc) internal {
        a.tf.last().setWinner(Machine.Hash.wrap(pc.getMachineMerkleRoot()));
        a.dc.stageTournamentResult(_epoch(a), pc.getMachineValidityProof());
    }

    function _acceptAfterPeriod(App memory a) internal {
        vm.roll(block.number + a.dc.getClaimStagingPeriod());
        a.dc.acceptStagedTournamentResult(_epoch(a));
    }

    /// @dev Stage the current machine as the epoch's winner and accept it after the staging period.
    function _settle(App memory a) internal returns (LibEmulator.ProofComponents memory pc) {
        pc = _pc();
        _stage(a, pc);
        _acceptAfterPeriod(a);
    }

    // ---- inputs ----

    /// @dev Full EvmAdvance input blobs of the InputAdded events among `logs`, in order.
    function _inputBlobs(Vm.Log[] memory logs) internal pure returns (bytes[] memory blobs) {
        uint256 n;
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].topics[0] == IInputBox.InputAdded.selector) ++n;
        }
        blobs = new bytes[](n);
        uint256 k;
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].topics[0] == IInputBox.InputAdded.selector) {
                blobs[k++] = abi.decode(logs[i].data, (bytes));
            }
        }
    }

    function _lastInputBlob() internal returns (bytes memory) {
        bytes[] memory blobs = _inputBlobs(vm.getRecordedLogs());
        require(blobs.length > 0, "no InputAdded recorded");
        return blobs[blobs.length - 1];
    }
}
