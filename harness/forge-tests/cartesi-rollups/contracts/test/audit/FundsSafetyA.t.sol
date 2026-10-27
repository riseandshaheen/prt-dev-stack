// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area A: the settlement gate (P0).
// Invariant: no outputs root becomes executable except through acceptStagedTournamentResult.
pragma solidity ^0.8.30;

import {Vm} from "forge-std-1.9.6/src/Vm.sol";

import {LeafProof} from "cartesi-rollups-contracts-3.0.0/src/common/LeafProof.sol";
import {MachineValidationErrors} from "cartesi-rollups-contracts-3.0.0/src/common/MachineValidationErrors.sol";
import {MachineValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/MachineValidityProof.sol";
import {OutputValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/OutputValidityProof.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";
import {EmulatorConstants} from "cartesi-machine-solidity-step-0.15.0/src/EmulatorConstants.sol";

import {ITournament} from "prt-contracts/ITournament.sol";
import {Machine} from "prt-contracts/types/Machine.sol";

import {IDaveConsensus} from "src/IDaveConsensus.sol";

import {FundsSafetyBase} from "./FundsSafetyBase.sol";

contract FundsSafetyATest is FundsSafetyBase {
    using LibEmulator for LibEmulator.State;
    using LibEmulator for LibEmulator.ProofComponents;

    address internal constant USER = address(0xD00D);

    // A1: a staged-but-not-accepted root never validates an output. APP:337-349, DC:183-191.
    function testA1_stagedRootDoesNotValidateOutputs() external {
        App memory a = _deploy(1000);
        vm.deal(address(a.app), 10 ether);
        _resetMachine();
        uint64 idx = _addOutput(_voucher(USER, 1 ether, ""));
        LibEmulator.ProofComponents memory pc = _pc();
        _stage(a, pc);

        (,,,, bool staged,,, bytes32 stagedRoot) = a.dc.getCurrentSealedEpoch();
        assertTrue(staged, "staged");
        assertEq(stagedRoot, pc.outputsMerkleRoot, "staged root is the machine's tx-buffer root");
        assertFalse(a.dc.isOutputsMerkleRootValid(address(a.app), stagedRoot), "staging writes nothing to the set");

        OutputValidityProof memory p = _outputProof(idx);
        vm.expectRevert(abi.encodeWithSelector(IApplication.InvalidOutputsMerkleRoot.selector, stagedRoot));
        a.app.executeOutput(_voucher(USER, 1 ether, ""), p);
        assertEq(USER.balance, 0, "no value moved");
    }

    // A2: unanimous sentry claims without an accept call never write the set. DC:193-216.
    function testA2_sentryClaimsAloneDoNotValidateOutputs() external {
        address[] memory sentries = new address[](2);
        sentries[0] = address(0x51);
        sentries[1] = address(0x52);
        App memory a = _deploy(1000, sentries);
        vm.deal(address(a.app), 10 ether);
        _resetMachine();
        uint64 idx = _addOutput(_voucher(USER, 1 ether, ""));
        LibEmulator.ProofComponents memory pc = _pc();
        _stage(a, pc);

        Machine.Hash h = Machine.Hash.wrap(pc.getMachineMerkleRoot());
        vm.prank(sentries[0]);
        a.dc.submitSentryClaim(0, h);
        vm.prank(sentries[1]);
        a.dc.submitSentryClaim(0, h);
        (, bool allAgree, bool periodOver,,,) = a.dc.canAcceptStagedTournamentResult();
        assertTrue(allAgree, "all sentries agree");
        assertFalse(periodOver, "period not over, only unanimity would shorten it");
        assertFalse(a.dc.isOutputsMerkleRootValid(address(a.app), pc.outputsMerkleRoot), "claims write nothing");

        OutputValidityProof memory p = _outputProof(idx);
        vm.expectRevert(abi.encodeWithSelector(IApplication.InvalidOutputsMerkleRoot.selector, pc.outputsMerkleRoot));
        a.app.executeOutput(_voucher(USER, 1 ether, ""), p);
        assertEq(_epoch(a), 0, "epoch not sealed by claims");
    }

    // A3: ROOT_FAILED can never be staged; the epoch freezes and only the guardian can act. DC:174-176, 437-450.
    function testA3_rootFailedCannotBeStaged() external {
        App memory a = _deploy(1000);
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        a.tf.last().setFailed();
        MachineValidityProof memory mvp = pc.getMachineValidityProof(); // external lib call: build before expectRevert

        (bool finished, bool failed, bool staged,,,) = a.dc.canStageTournamentResult();
        assertTrue(finished && failed && !staged, "reported as finished without a winner");

        vm.expectRevert(ITournament.TournamentFailedNoWinner.selector);
        a.dc.stageTournamentResult(0, mvp);
        vm.expectRevert(IDaveConsensus.TournamentResultNotStaged.selector);
        a.dc.acceptStagedTournamentResult(0);
        assertEq(_epoch(a), 0, "epoch frozen");

        // Nobody but the guardian can move the application out of the frozen state.
        vm.prank(address(0x1234));
        vm.expectRevert(IApplication.NotGuardian.selector);
        a.app.foreclose();
        vm.prank(GUARDIAN);
        a.app.foreclose();
        assertTrue(a.app.isForeclosed());
        // After foreclosure the consensus is terminal: even a later winner cannot be staged.
        a.tf.last().setWinner(Machine.Hash.wrap(pc.getMachineMerkleRoot()));
        vm.expectRevert(_encodeApplicationForeclosed(address(a.app)));
        a.dc.stageTournamentResult(0, mvp);
    }

    // A4: the outputs root cannot be detached from the winner's machine hash. DC:456-462, LibMachineValidityProof:24-43.
    function testA4_stageRejectsProofsNotBoundToWinner() external {
        App memory a = _deploy(1000);

        // (a) proof built for a different machine than the winner's final state
        _resetMachine();
        _addOutput(_voucher(USER, 1 ether, ""));
        LibEmulator.ProofComponents memory winner = _pc();
        _resetMachine();
        _addOutput(_voucher(USER, 99 ether, ""));
        LibEmulator.ProofComponents memory other = _pc();
        assertTrue(winner.getMachineMerkleRoot() != other.getMachineMerkleRoot());
        a.tf.last().setWinner(Machine.Hash.wrap(winner.getMachineMerkleRoot()));
        MachineValidityProof memory otherProof = other.getMachineValidityProof();
        vm.expectRevert(MachineValidationErrors.InvalidMachineMerkleProof.selector);
        a.dc.stageTournamentResult(0, otherProof);

        // (b) a valid leaf proof presented at the wrong address (iflags.Y leaf used as the tx-buffer leaf)
        MachineValidityProof memory swapped = winner.getMachineValidityProof();
        LeafProof memory y = swapped.iflagsYProof;
        swapped.txBufferProof = y;
        vm.expectRevert(MachineValidationErrors.InvalidMachineMerkleProof.selector);
        a.dc.stageTournamentResult(0, swapped);

        // (c) iflags.Y == 0: the machine is not yielded
        _resetMachine();
        _emu.setIflagsY(0);
        _addOutput(_voucher(USER, 1 ether, ""));
        LibEmulator.ProofComponents memory notYielded = _pc();
        a.tf.last().setWinner(Machine.Hash.wrap(notYielded.getMachineMerkleRoot()));
        MachineValidityProof memory notYieldedProof = notYielded.getMachineValidityProof();
        vm.expectRevert(MachineValidationErrors.InvalidPostEpochMachineIflagsYRegister.selector);
        a.dc.stageTournamentResult(0, notYieldedProof);

        // (d) manual yield with reason RX_REJECTED instead of RX_ACCEPTED
        _resetMachine();
        _emu.setHtifTohost(
            EmulatorConstants.HTIF_DEV_YIELD,
            EmulatorConstants.HTIF_YIELD_CMD_MANUAL,
            EmulatorConstants.HTIF_YIELD_MANUAL_REASON_RX_REJECTED
        );
        _addOutput(_voucher(USER, 1 ether, ""));
        LibEmulator.ProofComponents memory rejected = _pc();
        a.tf.last().setWinner(Machine.Hash.wrap(rejected.getMachineMerkleRoot()));
        MachineValidityProof memory rejectedProof = rejected.getMachineValidityProof();
        vm.expectRevert(MachineValidationErrors.InvalidPostEpochMachineHtifTohostRegister.selector);
        a.dc.stageTournamentResult(0, rejectedProof);

        (,,,, bool staged,,,) = a.dc.getCurrentSealedEpoch();
        assertFalse(staged, "nothing staged");
    }

    // A5: epoch-number pinning and no double accept. DC:243-249.
    function testA5_acceptEpochPinningAndNoDoubleAccept() external {
        App memory a = _deploy(1);
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        MachineValidityProof memory mvp = pc.getMachineValidityProof();
        _stage(a, pc);
        vm.roll(block.number + 1);

        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.IncorrectEpochNumber.selector, uint256(1), uint256(0)));
        a.dc.acceptStagedTournamentResult(1);
        a.dc.acceptStagedTournamentResult(0);
        assertEq(_epoch(a), 1);

        // second accept: the old epoch number is rejected, the new one is unstaged
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.IncorrectEpochNumber.selector, uint256(0), uint256(1)));
        a.dc.acceptStagedTournamentResult(0);
        vm.expectRevert(IDaveConsensus.TournamentResultNotStaged.selector);
        a.dc.acceptStagedTournamentResult(1);
        // and the old epoch cannot be re-staged either
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.IncorrectEpochNumber.selector, uint256(0), uint256(1)));
        a.dc.stageTournamentResult(0, mvp);
    }

    // A6: N > 0 sentries with N-1 claims do not shorten the staging period. DC:250-256, 464-466.
    function testA6_partialSentryAgreementDoesNotShortenPeriod() external {
        address[] memory sentries = new address[](2);
        sentries[0] = address(0x51);
        sentries[1] = address(0x52);
        uint256 period = 10;
        App memory a = _deploy(period, sentries);
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        _stage(a, pc);
        Machine.Hash h = Machine.Hash.wrap(pc.getMachineMerkleRoot());
        vm.prank(sentries[0]);
        a.dc.submitSentryClaim(0, h);
        assertEq(a.dc.getSentryClaimCount(0, h), 1);

        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.ClaimStagingPeriodNotOverYet.selector, uint256(0), period));
        a.dc.acceptStagedTournamentResult(0);
        vm.roll(block.number + period - 1);
        vm.expectRevert(
            abi.encodeWithSelector(IDaveConsensus.ClaimStagingPeriodNotOverYet.selector, period - 1, period)
        );
        a.dc.acceptStagedTournamentResult(0);
        vm.roll(block.number + 1);
        a.dc.acceptStagedTournamentResult(0);
        assertEq(_epoch(a), 1, "accepted exactly at the period boundary (inclusive)");

        // control: with the last claim in, the period is skipped in the same block
        _resetMachine();
        _addOutput(_notice("x"));
        LibEmulator.ProofComponents memory pc2 = _pc();
        _stage(a, pc2);
        Machine.Hash h2 = Machine.Hash.wrap(pc2.getMachineMerkleRoot());
        vm.prank(sentries[0]);
        a.dc.submitSentryClaim(1, h2);
        vm.prank(sentries[1]);
        a.dc.submitSentryClaim(1, h2);
        a.dc.acceptStagedTournamentResult(1);
        assertEq(_epoch(a), 2, "unanimity shortens the wait to zero");
    }

    // A7: the data provider binds the tournament to [lower, upper). DC:370-390.
    function testA7_provideMerkleRootOfInputBounds() external {
        App memory a = _deploy(1);
        // two inputs land in the accumulating epoch; epoch 0 is [0, 0)
        vm.recordLogs();
        _contracts.core.inputBox.addInput(address(a.app), "first");
        _contracts.core.inputBox.addInput(address(a.app), "second");
        bytes[] memory blobs = _inputBlobs(vm.getRecordedLogs());
        assertEq(blobs.length, 2);

        // in epoch 0 every index is out of bounds
        assertEq(a.dc.provideMerkleRootOfInput(0, blobs[0]), bytes32(0), "epoch 0 has no inputs: fixpoint");

        _resetMachine();
        _settle(a); // epoch 1 = [0, 2)
        (, uint256 lower, uint256 upper,,,,,) = a.dc.getCurrentSealedEpoch();
        assertEq(lower, 0);
        assertEq(upper, 2);

        bytes32 r0 = a.dc.provideMerkleRootOfInput(0, blobs[0]);
        bytes32 r1 = a.dc.provideMerkleRootOfInput(1, blobs[1]);
        assertTrue(r0 != bytes32(0) && r1 != bytes32(0) && r0 != r1, "in-range inputs have drive roots");
        assertEq(a.dc.provideMerkleRootOfInput(2, blobs[1]), bytes32(0), "index == upper: zero root (fixpoint)");
        assertEq(a.dc.provideMerkleRootOfInput(type(uint256).max - 1, ""), bytes32(0), "far out of range: zero root");

        bytes32 realHash = _contracts.core.inputBox.getInputHash(address(a.app), 0);
        vm.expectRevert(
            abi.encodeWithSelector(IDaveConsensus.InputHashMismatch.selector, keccak256(blobs[1]), realHash)
        );
        a.dc.provideMerkleRootOfInput(0, blobs[1]);
        vm.expectRevert(
            abi.encodeWithSelector(IDaveConsensus.InputHashMismatch.selector, keccak256("garbage"), realHash)
        );
        a.dc.provideMerkleRootOfInput(0, "garbage");
    }

    // A9: the accepted-roots set is monotone; proofs carry no epoch. APP:328-351.
    function testA9_oldEpochOutputExecutesAfterLaterEpochsSettle() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);

        // epoch 0 settles with state S1 = [voucher 1 ETH to USER]
        _resetMachine();
        bytes memory v0 = _voucher(USER, 1 ether, "");
        uint64 i0 = _addOutput(v0);
        LibEmulator.ProofComponents memory s1 = _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0); // proof against S1's outputs root

        // epochs 1..3 settle with other states
        for (uint256 k = 1; k <= 3; ++k) {
            _resetMachine();
            _addOutput(v0);
            _addOutput(_notice(abi.encode(k)));
            LibEmulator.ProofComponents memory sk = _settle(a);
            assertTrue(sk.outputsMerkleRoot != s1.outputsMerkleRoot);
        }
        assertEq(_epoch(a), 4);
        assertTrue(a.dc.isOutputsMerkleRootValid(address(a.app), s1.outputsMerkleRoot), "epoch-1 root still valid");

        a.app.executeOutput(v0, p0);
        assertEq(USER.balance, 1 ether, "executes against the old root by design");
        assertTrue(a.app.wasOutputExecuted(i0));
    }
}
