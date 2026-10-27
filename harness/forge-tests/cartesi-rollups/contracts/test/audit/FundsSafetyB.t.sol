// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area B: output execution and replay (P0).
// Invariant: each output executes at most once, exactly as encoded, and only vouchers move value.
pragma solidity ^0.8.30;

import {IERC20} from "@openzeppelin-contracts-5.2.0/token/ERC20/IERC20.sol";

import {AddressErrors} from "cartesi-rollups-contracts-3.0.0/src/common/AddressErrors.sol";
import {BinaryMerkleTreeErrors} from "cartesi-rollups-contracts-3.0.0/src/common/BinaryMerkleTreeErrors.sol";
import {OutputValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/OutputValidityProof.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";

import {FundsSafetyBase} from "./FundsSafetyBase.sol";

/// @dev Voucher destination that re-enters executeOutput once with a prepared (output, proof).
contract Reenterer {
    IApplication public app;
    bytes public output;
    OutputValidityProof internal _proof;
    bool public armed;
    bool public innerOk;
    bytes public innerData;
    uint256 public received;

    function arm(IApplication a, bytes calldata o, OutputValidityProof calldata p) external {
        app = a;
        output = o;
        _proof = p;
        armed = true;
    }

    receive() external payable {
        received += msg.value;
        if (armed) {
            armed = false;
            (innerOk, innerData) = address(app).call(abi.encodeCall(IApplication.executeOutput, (output, _proof)));
        }
    }
}

/// @dev ERC-20 whose transfer returns false without reverting (B4).
contract FalseReturnToken {
    mapping(address => uint256) public balanceOf;
    uint256 public transferCalls;

    function mint(address to, uint256 v) external {
        balanceOf[to] += v;
    }

    function transfer(address, uint256) external returns (bool) {
        ++transferCalls;
        return false;
    }
}

/// @dev Destination that reverts until fixed (B5).
contract FixableDestination {
    error Broken(uint256 code);

    bool public fixedUp;
    uint256 public calls;

    function fix() external {
        fixedUp = true;
    }

    function act() external payable {
        if (!fixedUp) revert Broken(42);
        ++calls;
    }
}

/// @dev delegatecall target that writes arbitrary storage slots of the caller (B6).
contract SlotWriter {
    function write(uint256 slot, bytes32 value) external {
        assembly {
            sstore(slot, value)
        }
    }
}

interface IOwnable {
    function owner() external view returns (address);
}

contract FundsSafetyBTest is FundsSafetyBase {
    using LibEmulator for LibEmulator.State;
    using LibEmulator for LibEmulator.ProofComponents;

    address internal constant USER = address(0xD00D);

    // B1: same voucher twice, and re-entry with the same index from the destination. APP:171-179.
    function testB1_sameOutputCannotExecuteTwiceEvenUnderReentrancy() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);
        Reenterer r = new Reenterer();
        _resetMachine();
        bytes memory v0 = _voucher(address(r), 1 ether, "");
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);

        // re-entry with the same index during the first execution
        r.arm(a.app, v0, p0);
        a.app.executeOutput(v0, p0);
        assertFalse(r.innerOk(), "inner re-execution reverted");
        assertEq(r.innerData(), abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v0));
        assertEq(r.received(), 1 ether, "paid exactly once");
        assertEq(a.app.getNumberOfExecutedOutputs(), 1);

        // plain second execution
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v0));
        a.app.executeOutput(v0, p0);
        assertEq(address(a.app).balance, 9 ether);
    }

    // B2: re-entry with a *different* valid output succeeds by design; no double spend. APP:177-184.
    function testB2_reentryWithDifferentOutputMovesExactlyTheSum() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);
        Reenterer r = new Reenterer();
        _resetMachine();
        bytes memory v0 = _voucher(address(r), 1 ether, "");
        bytes memory v1 = _voucher(address(r), 2 ether, "");
        uint64 i0 = _addOutput(v0);
        uint64 i1 = _addOutput(v1);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);
        OutputValidityProof memory p1 = _outputProof(i1);

        r.arm(a.app, v1, p1);
        a.app.executeOutput(v0, p0);
        assertTrue(r.innerOk(), "nested execution of a different output succeeds");
        assertEq(r.received(), 3 ether, "total moved = v0 + v1");
        assertEq(address(a.app).balance, 7 ether);
        assertEq(a.app.getNumberOfExecutedOutputs(), 2);
        assertTrue(a.app.wasOutputExecuted(i0) && a.app.wasOutputExecuted(i1));

        // neither can be replayed afterwards
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v0));
        a.app.executeOutput(v0, p0);
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v1));
        a.app.executeOutput(v1, p1);
    }

    // B3: value > balance reverts InsufficientFunds, bit rolled back, retryable once funded. LibAddress:24-26.
    function testB3_insufficientFundsRollsBackAndIsRetryable() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 1 ether);
        _resetMachine();
        bytes memory v0 = _voucher(USER, 5 ether, "");
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);

        vm.expectRevert(abi.encodeWithSelector(AddressErrors.InsufficientFunds.selector, 5 ether, 1 ether));
        a.app.executeOutput(v0, p0);
        assertFalse(a.app.wasOutputExecuted(i0), "bit rolled back with the revert");
        assertEq(a.app.getNumberOfExecutedOutputs(), 0);

        vm.deal(address(a.app), 5 ether);
        a.app.executeOutput(v0, p0);
        assertEq(USER.balance, 5 ether);
        assertTrue(a.app.wasOutputExecuted(i0));
    }

    // B4: ERC-20 transfer returning false is treated as success; output consumed, tokens not moved.
    // Documented funds-safety gap: safeCall (LibAddress:20-40) ignores return data. APP:673-681.
    function testB4_falseReturningErc20VoucherIsConsumedWithoutMovingTokens() external {
        App memory a = _deploy(1);
        FalseReturnToken t = new FalseReturnToken();
        t.mint(address(a.app), 1000);
        _resetMachine();
        bytes memory v0 = _voucher(address(t), 0, abi.encodeCall(IERC20.transfer, (USER, 1000)));
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);

        a.app.executeOutput(v0, p0); // does not revert
        assertEq(t.transferCalls(), 1, "token was called");
        assertEq(t.balanceOf(address(a.app)), 1000, "tokens never left the app");
        assertEq(t.balanceOf(USER), 0);
        assertTrue(a.app.wasOutputExecuted(i0), "but the output is marked executed");
        emit log_string("B4 observed: false-returning ERC-20 voucher executes 'successfully'; tokens stay, output spent");

        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v0));
        a.app.executeOutput(v0, p0);
    }

    // B5: destination reverts -> bubbled data, bit cleared; retry after fix succeeds once. APP:653-669.
    function testB5_revertingDestinationBubblesAndRetrySucceedsOnce() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);
        FixableDestination d = new FixableDestination();
        _resetMachine();
        bytes memory v0 = _voucher(address(d), 1 ether, abi.encodeCall(FixableDestination.act, ()));
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);

        vm.expectRevert(abi.encodeWithSelector(FixableDestination.Broken.selector, 42));
        a.app.executeOutput(v0, p0);
        assertFalse(a.app.wasOutputExecuted(i0));
        assertEq(address(d).balance, 0);

        d.fix();
        a.app.executeOutput(v0, p0);
        assertEq(d.calls(), 1);
        assertEq(address(d).balance, 1 ether);
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotReexecutable.selector, v0));
        a.app.executeOutput(v0, p0);
        assertEq(d.calls(), 1, "executed exactly once");
    }

    // B6: a DelegateCallVoucher can rewrite the Application's own storage: foreclosure flag, validator, owner.
    // Trust boundary: the consensus is trusted for every slot. APP:685-692, LibAddress:46-59.
    function testB6_delegateCallVoucherRewritesApplicationStorage() external {
        App memory a = _deploy(1);
        SlotWriter w = new SlotWriter();
        address attacker = address(0xA77AC);
        // slot 4 packs _outputsMerkleRootValidator (bytes 0..19) | _isForeclosed (byte 20) | _wasAccountsDriveMerkleRootProved (byte 21)
        bytes32 slot4 = bytes32(uint256(uint160(attacker)) | (uint256(1) << 160));
        _resetMachine();
        bytes memory d0 = _delegateCallVoucher(address(w), abi.encodeCall(SlotWriter.write, (4, slot4)));
        bytes memory d1 = _delegateCallVoucher(address(w), abi.encodeCall(SlotWriter.write, (0, bytes32(uint256(uint160(attacker))))));
        uint64 i0 = _addOutput(d0);
        uint64 i1 = _addOutput(d1);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);
        OutputValidityProof memory p1 = _outputProof(i1);

        assertEq(address(a.app.getOutputsMerkleRootValidator()), address(a.dc));
        assertFalse(a.app.isForeclosed());
        assertEq(IOwnable(address(a.app)).owner(), address(0), "factory renounced");

        a.app.executeOutput(d1, p1);
        assertEq(IOwnable(address(a.app)).owner(), attacker, "owner slot overwritten by the machine");
        // the validator rewrite goes last: afterwards no further output can even be validated
        a.app.executeOutput(d0, p0);
        assertEq(address(a.app.getOutputsMerkleRootValidator()), attacker, "validator slot overwritten by the machine");
        assertTrue(a.app.isForeclosed(), "foreclosure flag overwritten by the machine");
        vm.expectRevert(); // validator is now an EOA: every later executeOutput reverts
        a.app.executeOutput(d1, p1);
        emit log_string("B6 observed: DelegateCallVoucher rewrote slots 0 and 4 (owner, validator, foreclosed)");
        // A1/A4 show the only way to such a voucher is an accepted root bound to the winner's machine hash.
    }

    // B7: Notice or unknown selector -> OutputNotExecutable, no state change. APP:660, 667.
    function testB7_noticeAndUnknownSelectorAreNotExecutable() external {
        App memory a = _deploy(1);
        _resetMachine();
        bytes memory n0 = _notice("hello");
        bytes memory u1 = abi.encodeWithSelector(bytes4(0xdeadbeef), USER, uint256(1 ether));
        bytes memory s2 = hex"aabb"; // shorter than a selector
        uint64 i0 = _addOutput(n0);
        uint64 i1 = _addOutput(u1);
        uint64 i2 = _addOutput(s2);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);
        OutputValidityProof memory p1 = _outputProof(i1);
        OutputValidityProof memory p2 = _outputProof(i2);

        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotExecutable.selector, n0));
        a.app.executeOutput(n0, p0);
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotExecutable.selector, u1));
        a.app.executeOutput(u1, p1);
        vm.expectRevert(abi.encodeWithSelector(IApplication.OutputNotExecutable.selector, s2));
        a.app.executeOutput(s2, p2);
        assertEq(a.app.getNumberOfExecutedOutputs(), 0);
        assertFalse(a.app.wasOutputExecuted(i0) || a.app.wasOutputExecuted(i1) || a.app.wasOutputExecuted(i2));
    }

    // B8: siblings length != 63 and outputIndex with bits above the tree height. LibOutputValidityProof:15-30.
    function testB8_malformedOutputProofsRevert() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);
        _resetMachine();
        bytes memory v0 = _voucher(USER, 1 ether, "");
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory good = _outputProof(i0);
        assertEq(good.outputHashesSiblings.length, 63);

        // 62 siblings
        OutputValidityProof memory short_ = _outputProof(i0);
        bytes32[] memory s62 = new bytes32[](62);
        for (uint256 i; i < 62; ++i) s62[i] = good.outputHashesSiblings[i];
        short_.outputHashesSiblings = s62;
        vm.expectRevert(IApplication.InvalidOutputHashesSiblingsArrayLength.selector);
        a.app.executeOutput(v0, short_);

        // 64 siblings
        OutputValidityProof memory long_ = _outputProof(i0);
        bytes32[] memory s64 = new bytes32[](64);
        for (uint256 i; i < 63; ++i) s64[i] = good.outputHashesSiblings[i];
        long_.outputHashesSiblings = s64;
        vm.expectRevert(IApplication.InvalidOutputHashesSiblingsArrayLength.selector);
        a.app.executeOutput(v0, long_);

        // index with bit 63 set: outside a height-63 tree
        OutputValidityProof memory high = _outputProof(i0);
        high.outputIndex = uint64(1) << 63;
        vm.expectRevert(
            abi.encodeWithSelector(BinaryMerkleTreeErrors.InvalidNodeIndex.selector, uint256(1) << 63, uint256(63))
        );
        a.app.executeOutput(v0, high);

        // index of a different leaf with these siblings: root mismatch
        OutputValidityProof memory wrongLeaf = _outputProof(i0);
        wrongLeaf.outputIndex = i0 + 1;
        vm.expectRevert(); // InvalidOutputsMerkleRoot(<some other root>)
        a.app.executeOutput(v0, wrongLeaf);

        a.app.executeOutput(v0, good);
        assertEq(USER.balance, 1 ether);
    }

    // B9: executeOutput has no foreclosure check; accepted vouchers remain executable after foreclose().
    // APP:163-184 vs 189, 220, 260. (The double-exit measurement is F10.)
    function testB9_acceptedVoucherExecutesAfterForeclosure() external {
        App memory a = _deploy(1);
        vm.deal(address(a.app), 10 ether);
        _resetMachine();
        bytes memory v0 = _voucher(USER, 1 ether, "");
        uint64 i0 = _addOutput(v0);
        _settle(a);
        OutputValidityProof memory p0 = _outputProof(i0);

        vm.prank(GUARDIAN);
        a.app.foreclose();
        assertTrue(a.app.isForeclosed());

        a.app.executeOutput(v0, p0);
        assertEq(USER.balance, 1 ether, "voucher of an accepted epoch paid after foreclosure");
        emit log_string("B9 observed: executeOutput succeeds on a foreclosed app; it shares the pool with withdraw/issueRefund");
    }
}
