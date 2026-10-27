// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area D: bond and refund accounting (P2).
// Real Tournament.sol clones (small single-/two-level geometries, stub state transition) driven by
// test/fixtures/TournamentDriver.sol. D7's padding case uses the real CartesiStateTransition.
pragma solidity ^0.8.17;

import {Vm} from "forge-std-1.9.6/src/Vm.sol";

import {IDataProvider} from "src/IDataProvider.sol";
import {ITournament} from "src/ITournament.sol";
import {CartesiStateTransition} from "src/state-transition/CartesiStateTransition.sol";
import {Bond} from "src/tournament/libs/Bond.sol";
import {Clock} from "src/tournament/libs/Clock.sol";
import {Gas} from "src/tournament/libs/Gas.sol";
import {Match} from "src/tournament/libs/Match.sol";
import {Time} from "src/tournament/libs/Time.sol";
import {Tree} from "src/types/Tree.sol";

import {InspectableTournament} from "test/fixtures/InspectableTournament.sol";
import {SmallFullTree} from "test/fixtures/SmallFullTree.sol";
import {TournamentDriver} from "test/fixtures/TournamentDriver.sol";
import {TournamentInspector} from "test/fixtures/TournamentInspector.sol";

/// @dev IDataProvider that reports "no input" for every index (zero root fixpoint).
contract NoInputProvider is IDataProvider {
    function provideMerkleRootOfInput(uint256, bytes calldata) external pure override returns (bytes32) {
        return bytes32(0);
    }
}

/// @dev Sends two refundable actions in one transaction (D7 batching).
contract Batcher {
    uint256 public received;

    receive() external payable {
        received += msg.value;
    }

    function two(ITournament t, bytes calldata a, bytes calldata b) external {
        (bool ok1,) = address(t).call(a);
        require(ok1, "a");
        (bool ok2,) = address(t).call(b);
        require(ok2, "b");
    }
}

contract FundsSafetyDTest is TournamentDriver {
    using SmallFullTree for SmallFullTree.Data;
    using Tree for Tree.Node;
    using Time for Time.Instant;
    using Match for Match.Id;
    using TournamentInspector for InspectableTournament;

    uint64 constant G = 5;
    uint64 constant T = 20;
    uint64 constant ALLOWANCE = 200;

    struct Pair {
        InspectableTournament t;
        SmallFullTree.Data h;
        SmallFullTree.Data s;
    }

    function _deadline(InspectableTournament t, Tree.Node root) internal view returns (uint64) {
        Clock.State memory c = clockOf(t, root);
        return Time.Instant.unwrap(c.startInstant.add(c.allowance));
    }

    /// @dev Single-level root with two joiners; honest has responded so the Sybil clock runs.
    function _pairAfterOneAdvance() internal returns (Pair memory p) {
        vm.roll(100);
        p.t = newRoot(singleLevelFactory(G, ALLOWANCE));
        p.h = seededTree("honest", 3);
        p.s = seededTree("sybil", 3);
        join(p.t, p.h, HONEST);
        join(p.t, p.s, SYBIL);
        advance(p.t, p.h, p.s);
    }

    function _refundEvents(Vm.Log[] memory logs, address emitter)
        internal
        pure
        returns (uint256 n, uint256[] memory values, bool[] memory ok)
    {
        values = new uint256[](logs.length);
        ok = new bool[](logs.length);
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].emitter == emitter && logs[i].topics[0] == ITournament.PartialBondRefund.selector) {
                values[n] = abi.decode(logs[i].data, (uint256));
                ok[n] = uint256(logs[i].topics[2]) == 1;
                ++n;
            }
        }
    }

    // D1: tryRecoveringBond while running, with no winner, after recovery. T:416-454.
    function testD1_tryRecoveringBondStates() external {
        Pair memory p = _pairAfterOneAdvance();
        vm.expectRevert(ITournament.TournamentNotFinished.selector);
        p.t.tryRecoveringBond();
        (ITournament.BondDisposition disp,,) = p.t.bondRecovery();
        assertEq(uint8(disp), uint8(ITournament.BondDisposition.TOURNAMENT_RUNNING));

        // branch 1: no winner (both expire, pair eliminated)
        uint256 snap = vm.snapshotState();
        rollTo(_deadline(p.t, p.s.root()) + allowanceOf(p.t, p.h.root()));
        assertEq(uint8(classify(p.t, p.h, p.s)), uint8(ITournament.MatchTimeoutOutcome.ELIMINATE_BOTH));
        vm.prank(THIRD);
        eliminate(p.t, p.h, p.s);
        if (closureOf(p.t) > block.number) rollTo(closureOf(p.t));
        assertEq(uint8(standing(p.t)), uint8(ITournament.TournamentStanding.ROOT_FAILED));
        vm.expectRevert(ITournament.NoWinner.selector);
        p.t.tryRecoveringBond();
        (disp,,) = p.t.bondRecovery();
        assertEq(uint8(disp), uint8(ITournament.BondDisposition.NO_WINNER));
        vm.revertToState(snap);

        // branch 2: winner, recover, then no-op
        rollTo(_deadline(p.t, p.s.root()));
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        rollTo(closureOf(p.t));
        assertEq(uint8(standing(p.t)), uint8(ITournament.TournamentStanding.ROOT_WINNER));
        uint256 hb = HONEST.balance;
        assertTrue(p.t.tryRecoveringBond());
        assertGt(HONEST.balance, hb);
        assertEq(address(p.t).balance, 0);
        (disp,,) = p.t.bondRecovery();
        assertEq(uint8(disp), uint8(ITournament.BondDisposition.RECOVERED));
        uint256 hb2 = HONEST.balance;
        assertTrue(p.t.tryRecoveringBond(), "second call: true as a no-op");
        assertEq(HONEST.balance, hb2);
        assertEq(address(p.t).balance, 0);
    }

    // D2: winner payout arithmetic. T:435-449.
    function testD2_winnerPayoutArithmetic() external {
        // (a) balance <= bond: a lone claimer, no match, closure -> ROOT_WINNER; full balance paid, nothing burned
        vm.roll(100);
        InspectableTournament lone = newRoot(singleLevelFactory(G, ALLOWANCE));
        SmallFullTree.Data memory h = seededTree("honest", 3);
        join(lone, h, HONEST);
        uint256 bond = lone.bondValue();
        assertEq(address(lone).balance, bond);
        rollTo(closureOf(lone));
        uint256 burned0 = address(0).balance;
        uint256 hb = HONEST.balance;
        assertTrue(lone.tryRecoveringBond());
        assertEq(HONEST.balance - hb, bond, "balance <= bond: full balance");
        assertEq(address(0).balance, burned0, "nothing burned");
        assertEq(address(lone).balance, 0);

        // (b) balance > bond: two bonds pooled, timeout win at gas price 0 (no refund) -> bond + (2bond-bond)/10, rest burned
        vm.txGasPrice(0);
        vm.fee(0);
        Pair memory p = _pairAfterOneAdvance();
        rollTo(_deadline(p.t, p.s.root()));
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        rollTo(closureOf(p.t));
        uint256 balance = address(p.t).balance;
        bond = p.t.bondValue();
        assertEq(balance, 2 * bond, "two bonds, zero refunds at gas price 0");
        (ITournament.BondDisposition disp, address claimer, uint256 payment) = p.t.bondRecovery();
        assertEq(uint8(disp), uint8(ITournament.BondDisposition.RECOVERABLE));
        assertEq(claimer, HONEST);
        assertEq(payment, bond + (balance - bond) / 10);
        burned0 = address(0).balance;
        hb = HONEST.balance;
        assertTrue(p.t.tryRecoveringBond());
        assertEq(HONEST.balance - hb, bond + (balance - bond) / 10, "bond + a tenth of the surplus");
        assertEq(address(0).balance - burned0, balance - payment, "the rest burned to address(0)");
        assertEq(address(p.t).balance, 0);
        emit log_named_uint("D2 bond", bond);
        emit log_named_uint("D2 winner payment", payment);
        emit log_named_uint("D2 burned", balance - payment);
    }

    // D6: refund = min(balance, allocation x 50 gwei, units x min(gasprice, basefee + 10 gwei)). T:1292-1312.
    function testD6_refundCaps() external {
        Pair memory p = _pairAfterOneAdvance();
        rollTo(_deadline(p.t, p.s.root()));
        uint256 cap = Bond.actionRefundCap(Gas.WIN_MATCH_BY_TIMEOUT);
        uint256 balance = address(p.t).balance;
        assertGt(balance, cap, "two bonds exceed one action cap");

        // measure units at unit price
        uint256 snap = vm.snapshotState();
        vm.fee(0);
        vm.txGasPrice(1);
        vm.recordLogs();
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        (uint256 n, uint256[] memory v,) = _refundEvents(vm.getRecordedLogs(), address(p.t));
        assertEq(n, 1);
        uint256 units = v[0];
        assertGt(units, Gas.TX);
        assertLt(units, Gas.WIN_MATCH_BY_TIMEOUT);
        vm.revertToState(snap);

        // (a) gas price far above basefee + 10 gwei: priced at basefee + 10 gwei
        snap = vm.snapshotState();
        vm.fee(1 gwei);
        vm.txGasPrice(500 gwei);
        uint256 hb = HONEST.balance;
        vm.recordLogs();
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        (n, v,) = _refundEvents(vm.getRecordedLogs(), address(p.t));
        assertEq(v[0], units * 11 gwei, "priority above 10 gwei is not refunded");
        assertLt(v[0], cap);
        assertEq(HONEST.balance - hb, units * 11 gwei);
        vm.revertToState(snap);

        // (b) price high enough that units x price exceeds the allocation cap: refund = cap
        snap = vm.snapshotState();
        vm.fee(5000 gwei);
        vm.txGasPrice(5000 gwei);
        assertGt(units * 5000 gwei, cap);
        hb = HONEST.balance;
        vm.recordLogs();
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        (n, v,) = _refundEvents(vm.getRecordedLogs(), address(p.t));
        assertEq(v[0], cap, "capped at allocation x 50 gwei");
        assertEq(HONEST.balance - hb, cap);
        vm.revertToState(snap);

        // (c) balance below the priced work: refund = balance
        vm.fee(1 gwei);
        vm.txGasPrice(1 gwei);
        uint256 tiny = units * 1 gwei - 1;
        vm.deal(address(p.t), tiny);
        hb = HONEST.balance;
        vm.recordLogs();
        vm.prank(HONEST);
        timeoutWin(p.t, p.h, p.s, p.h);
        (n, v,) = _refundEvents(vm.getRecordedLogs(), address(p.t));
        assertEq(v[0], tiny, "capped at the pool balance");
        assertEq(address(p.t).balance, 0);
        emit log_named_uint("D6 measured units (winMatchByTimeout)", units);
        emit log_named_uint("D6 action cap (wei)", cap);
    }

    // D7: refund farming. T:582, docs/prt-refund-accounting.md:169-182.
    function testD7_refundFarming() external {
        // (a) padded leaf-proof bytes: the real state transition rejects bytes where no input exists
        CartesiStateTransition stf = new CartesiStateTransition();
        NoInputProvider none = new NoInputProvider();
        bytes memory padded = abi.encodePacked(uint64(100), new bytes(100));
        vm.expectRevert(abi.encodeWithSelector(CartesiStateTransition.InputBytesWithoutInput.selector, uint64(100)));
        stf.transitionState(bytes32(uint256(1)), 0, padded, none);
        // and a padded access log (trailing bytes after the input header) is rejected by exact consumption
        bytes memory trailing = abi.encodePacked(uint64(0), new bytes(64));
        vm.expectRevert();
        stf.transitionState(bytes32(uint256(1)), 0, trailing, none);

        // (b) batched actions in one tx: each refundable action earns the TX constant once
        vm.roll(100);
        InspectableTournament t = newRoot(singleLevelFactory(G, ALLOWANCE));
        SmallFullTree.Data memory h = seededTree("honest", 3);
        SmallFullTree.Data memory s1 = seededTree("sybil1", 3);
        SmallFullTree.Data memory s2 = seededTree("sybil2", 3);
        SmallFullTree.Data memory s3 = seededTree("sybil3", 3);
        join(t, h, HONEST);
        join(t, s1, SYBIL); // match (h, s1)
        join(t, s2, SYBIL_TWO);
        join(t, s3, THIRD); // match (s2, s3)
        advance(t, h, s1); // honest responds; s1's clock runs
        advance(t, s2, s3); // s2 responds; s3's clock runs
        uint64 d1 = _deadline(t, s1.root());
        uint64 d3 = _deadline(t, s3.root());
        rollTo(d1 > d3 ? d1 : d3);

        Batcher b = new Batcher();
        (,,, uint64 height) = t.tournamentLevelConstants();
        (Tree.Node hl, Tree.Node hr) = h.children(height, 0);
        (Tree.Node l2, Tree.Node r2) = s2.children(height, 0);
        vm.fee(0);
        vm.txGasPrice(1);
        vm.recordLogs();
        b.two(
            t,
            abi.encodeCall(ITournament.winMatchByTimeout, (matchId(h, s1), hl, hr)),
            abi.encodeCall(ITournament.winMatchByTimeout, (matchId(s2, s3), l2, r2))
        );
        (uint256 n, uint256[] memory v, bool[] memory ok) = _refundEvents(vm.getRecordedLogs(), address(t));
        assertEq(n, 2, "two refunds in one tx");
        assertTrue(ok[0] && ok[1]);
        assertGt(v[0], Gas.TX);
        assertGt(v[1], Gas.TX);
        assertEq(b.received(), v[0] + v[1]);
        emit log_named_uint("D7 refund units action 1 (incl. TX=25000)", v[0]);
        emit log_named_uint("D7 refund units action 2 (incl. TX=25000)", v[1]);
        emit log_string("D7 observed: TX constant paid per action in a batch (documented slack, RA:169-182)");
    }

    // D8: no-winner tournaments keep their balance forever. RA:108-109, 197-198.
    function testD8_noWinnerBalanceLockedForever() external {
        vm.fee(0);
        vm.txGasPrice(0);
        // two-level root; the dispute descends into a child; both child clocks expire
        vm.roll(100);
        InspectableTournament root = newRoot(twoLevelFactory(G, T, ALLOWANCE));
        Tree.Node[] memory honest = fineLeaves(1);
        Tree.Node[] memory sybil = fineLeaves(1, 10, 2);
        SmallFullTree.Data memory hRoot = rootTreeOf(honest);
        SmallFullTree.Data memory sRoot = rootTreeOf(sybil);
        join(root, hRoot, HONEST);
        join(root, sRoot, SYBIL);
        uint256 rootBond = root.bondValue();
        advanceToSeal(root, hRoot, sRoot);
        (InspectableTournament child, uint256 segment) = sealInner(root, hRoot, sRoot);
        SmallFullTree.Data memory hChild = childTreeOf(honest, segment);
        SmallFullTree.Data memory sChild = childTreeOf(sybil, segment);
        rollBy(T + 1);
        join(child, sChild, SYBIL);
        join(child, hChild, HONEST);
        uint256 childBond = child.bondValue();
        assertEq(address(child).balance, 2 * childBond);
        advance(child, sChild, hChild);

        // both expire -> eliminated by a third party -> child finishes with no winner
        Tree.Node runner = Match.responder(4, child.getMatch(matchId(sChild, hChild).hashFromId()).currentHeight)
            == ITournament.CommitmentSide.ONE ? sChild.root() : hChild.root();
        Tree.Node other = runner.eq(sChild.root()) ? hChild.root() : sChild.root();
        rollTo(_deadline(child, runner) + allowanceOf(child, other));
        assertEq(uint8(classify(child, sChild, hChild)), uint8(ITournament.MatchTimeoutOutcome.ELIMINATE_BOTH));
        vm.prank(THIRD);
        eliminate(child, sChild, hChild);
        if (closureOf(child) > block.number) rollTo(closureOf(child));
        assertEq(liveMatches(child), 0);
        vm.expectRevert(ITournament.NoWinner.selector);
        child.tryRecoveringBond();
        assertEq(address(child).balance, 2 * childBond, "child pool locked: no burn path, no recovery");

        // the parent eliminates the pair; the root then has no commitment and also fails
        vm.prank(THIRD);
        eliminateChild(root, child);
        assertEq(liveMatches(root), 0);
        if (closureOf(root) > block.number) rollTo(closureOf(root));
        assertEq(uint8(standing(root)), uint8(ITournament.TournamentStanding.ROOT_FAILED));
        vm.expectRevert(ITournament.NoWinner.selector);
        root.tryRecoveringBond();
        assertEq(address(root).balance, 2 * rootBond, "root pool locked too");

        emit log_named_uint("D8 locked per eliminated child pair (wei)", 2 * childBond);
        emit log_named_uint("D8 locked per eliminated root pair (wei)", 2 * rootBond);
        emit log_string("D8 observed: NoWinner forever; balance has no burn or recovery path (documented policy gap)");
    }
}
