// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area F: guardian, foreclosure and the emergency exit (P0).
pragma solidity ^0.8.30;

import {IERC20} from "@openzeppelin-contracts-5.2.0/token/ERC20/IERC20.sol";
import {IERC721} from "@openzeppelin-contracts-5.2.0/token/ERC721/IERC721.sol";
import {IERC1155} from "@openzeppelin-contracts-5.2.0/token/ERC1155/IERC1155.sol";
import {IERC20Errors} from "@openzeppelin-contracts-5.2.0/interfaces/draft-IERC6093.sol";

import {AccountValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/AccountValidityProof.sol";
import {OutputValidityProof} from "cartesi-rollups-contracts-3.0.0/src/common/OutputValidityProof.sol";
import {WithdrawalConfig} from "cartesi-rollups-contracts-3.0.0/src/common/WithdrawalConfig.sol";
import {ISafeErc20Transfer} from "cartesi-rollups-contracts-3.0.0/src/delegatecall/ISafeErc20Transfer.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {LibUsdAccount} from "cartesi-rollups-contracts-3.0.0/src/library/LibUsdAccount.sol";
import {IRefundOutputBuilderErrors} from "cartesi-rollups-contracts-3.0.0/src/refund/IRefundOutputBuilderErrors.sol";
import {IWithdrawalOutputBuilder} from "cartesi-rollups-contracts-3.0.0/src/withdrawal/IWithdrawalOutputBuilder.sol";
import {IWithdrawalOutputBuilderErrors} from "cartesi-rollups-contracts-3.0.0/src/withdrawal/IWithdrawalOutputBuilderErrors.sol";
import {AssetReceiver} from "cartesi-rollups-contracts-3.0.0/test/util/AssetReceiver.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";

import {Machine} from "prt-contracts/types/Machine.sol";

import {DaveAppFactory} from "src/DaveAppFactory.sol";
import {IDaveConsensus} from "src/IDaveConsensus.sol";

import {FundsSafetyBase, StubTournamentFactory} from "./FundsSafetyBase.sol";

contract FundsSafetyFTest is FundsSafetyBase {
    using LibEmulator for LibEmulator.State;
    using LibEmulator for LibEmulator.ProofComponents;

    address internal constant ALICE = address(0xA11);
    address internal constant BOB = address(0xB0B);

    function _foreclose(App memory a) internal {
        vm.prank(GUARDIAN);
        a.app.foreclose();
    }

    // F3: claimStagingPeriod = 0 with no sentries: stage and accept in one block (PRT-2 regression). DC:124, 251-255.
    function testF3_stagingPeriodZeroAcceptsInStagingBlock() external {
        App memory a = _deploy(0);
        assertEq(a.dc.getClaimStagingPeriod(), 0);
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        uint256 b0 = block.number;
        _stage(a, pc);
        (,,,,, uint256 stagingBlock,,) = a.dc.getCurrentSealedEpoch();
        a.dc.acceptStagedTournamentResult(0);
        assertEq(block.number, b0);
        assertEq(block.number - stagingBlock, 0, "guardian window = 0 blocks");
        assertEq(_epoch(a), 1);
        emit log_named_uint("F3 guardian_window_blocks", block.number - stagingBlock);
    }

    // F4: issueRefund for every portal kind returns the recorded amounts, once per input. APP:186-215, RefundOutputBuilder:63-81.
    function testF4_refundsEveryPortalKindExactlyOnce() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        vm.deal(ALICE, 100 ether);
        _contracts.dev.testFungibleToken.mint(ALICE, 1000);
        _contracts.dev.testNonFungibleToken.mint(ALICE, 7);
        _contracts.dev.testMultiToken.mint(ALICE, 11, 50);
        uint256[] memory ids = new uint256[](2);
        ids[0] = 21;
        ids[1] = 22;
        uint256[] memory vals = new uint256[](2);
        vals[0] = 5;
        vals[1] = 6;
        _contracts.dev.testMultiToken.mintBatch(ALICE, ids, vals);

        vm.startPrank(ALICE);
        _contracts.dev.testFungibleToken.approve(address(_contracts.core.erc20Portal), 1000);
        _contracts.dev.testNonFungibleToken.approve(address(_contracts.core.erc721Portal), 7);
        _contracts.dev.testMultiToken.setApprovalForAll(address(_contracts.core.erc1155SinglePortal), true);
        _contracts.dev.testMultiToken.setApprovalForAll(address(_contracts.core.erc1155BatchPortal), true);
        vm.recordLogs();
        _contracts.core.etherPortal.depositEther{value: 3 ether}(app, "");
        _contracts.core.erc20Portal.depositErc20Tokens(_contracts.dev.testFungibleToken, app, 1000, "");
        _contracts.core.erc721Portal.depositErc721Token(_contracts.dev.testNonFungibleToken, app, 7, "", "");
        _contracts.core.erc1155SinglePortal.depositSingleErc1155Token(_contracts.dev.testMultiToken, app, 11, 50, "", "");
        _contracts.core.erc1155BatchPortal.depositBatchErc1155Token(_contracts.dev.testMultiToken, app, ids, vals, "", "");
        vm.stopPrank();
        bytes[] memory inputs = _inputBlobs(vm.getRecordedLogs());
        assertEq(inputs.length, 5);

        // extra assets reach the app without inputs; refunds must still return the recorded amounts only
        vm.deal(app, app.balance + 50 ether);
        _contracts.dev.testFungibleToken.mint(app, 9999);

        vm.expectRevert(IApplication.NotForeclosed.selector);
        a.app.issueRefund(0, inputs[0]);
        _foreclose(a);

        uint256 aliceEth = ALICE.balance;
        for (uint256 i; i < 5; ++i) {
            a.app.issueRefund(i, inputs[i]);
            assertTrue(a.app.wasRefundForInputIssued(i));
        }
        assertEq(a.app.getNumberOfIssuedRefunds(), 5);
        assertEq(ALICE.balance - aliceEth, 3 ether, "ether: recorded value, not balance");
        assertEq(app.balance, 50 ether, "unrecorded ether stays");
        assertEq(_contracts.dev.testFungibleToken.balanceOf(ALICE), 1000, "erc20: recorded value");
        assertEq(_contracts.dev.testFungibleToken.balanceOf(app), 9999, "unrecorded erc20 stays");
        assertEq(_contracts.dev.testNonFungibleToken.ownerOf(7), ALICE, "erc721 returned");
        assertEq(_contracts.dev.testMultiToken.balanceOf(ALICE, 11), 50, "erc1155 single returned");
        assertEq(_contracts.dev.testMultiToken.balanceOf(ALICE, 21), 5, "erc1155 batch returned");
        assertEq(_contracts.dev.testMultiToken.balanceOf(ALICE, 22), 6, "erc1155 batch returned");

        for (uint256 i; i < 5; ++i) {
            vm.expectRevert(abi.encodeWithSelector(IApplication.RefundAlreadyIssued.selector, i));
            a.app.issueRefund(i, inputs[i]);
        }
    }

    // F5: finalized inputs and non-portal senders are not refundable. APP:200-202, DC:412-420.
    function testF5_finalizedInputAndUnknownSenderNotRefundable() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        vm.deal(ALICE, 10 ether);
        vm.recordLogs();
        vm.prank(ALICE);
        _contracts.core.etherPortal.depositEther{value: 1 ether}(app, ""); // input 0
        _contracts.core.inputBox.addInput(app, "not a deposit"); // input 1, sender = this test
        bytes[] memory inputs = _inputBlobs(vm.getRecordedLogs());

        // epoch 0 = [0,0) ; after one accept epoch 1 = [0,2) (not finalized) ; after two, epoch 2 = [2,2): 0 and 1 finalized
        _resetMachine();
        _settle(a);
        assertFalse(a.dc.wasInputFinalized(app, 0, 0));
        _resetMachine();
        _addOutput(_notice("2"));
        _settle(a);
        assertTrue(a.dc.wasInputFinalized(app, 0, 0));
        assertTrue(a.dc.wasInputFinalized(app, 1, 0));

        // a third, unfinalized deposit from a non-portal sender
        vm.recordLogs();
        _contracts.core.inputBox.addInput(app, "later");
        bytes memory later = _lastInputBlob();

        _foreclose(a);
        vm.expectRevert(abi.encodeWithSelector(IApplication.CannotRefundFinalizedInput.selector, 0));
        a.app.issueRefund(0, inputs[0]);
        vm.expectRevert(abi.encodeWithSelector(IApplication.CannotRefundFinalizedInput.selector, 1));
        a.app.issueRefund(1, inputs[1]);
        vm.expectRevert(abi.encodeWithSelector(IRefundOutputBuilderErrors.UnknownInputSender.selector, address(this)));
        a.app.issueRefund(2, later);
        // and a wrong blob for a valid index
        vm.expectRevert(
            abi.encodeWithSelector(
                IApplication.InvalidInputHash.selector, _contracts.core.inputBox.getInputHash(app, 2), keccak256(inputs[0])
            )
        );
        a.app.issueRefund(2, inputs[0]);
        assertEq(a.app.getNumberOfIssuedRefunds(), 0);
        assertEq(app.balance, 1 ether, "the finalized deposit is locked in the app");
    }

    // F6: a depositor that rejects Ether blocks only its own refund; retryable once it accepts. APP:193-208.
    function testF6_rejectingDepositorBlocksOnlyItself() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        AssetReceiver rejecter = new AssetReceiver();
        vm.deal(address(rejecter), 10 ether);
        vm.deal(BOB, 10 ether);
        vm.recordLogs();
        vm.prank(address(rejecter));
        _contracts.core.etherPortal.depositEther{value: 2 ether}(app, "");
        vm.prank(BOB);
        _contracts.core.etherPortal.depositEther{value: 1 ether}(app, "");
        bytes[] memory inputs = _inputBlobs(vm.getRecordedLogs());

        _foreclose(a);
        rejecter.setRejecting(true);
        vm.expectRevert(abi.encodeWithSelector(AssetReceiver.EtherRejected.selector, app, 2 ether));
        a.app.issueRefund(0, inputs[0]);
        assertFalse(a.app.wasRefundForInputIssued(0), "bit not set");

        a.app.issueRefund(1, inputs[1]);
        assertEq(BOB.balance, 10 ether, "other depositor unaffected");

        rejecter.setRejecting(false);
        a.app.issueRefund(0, inputs[0]);
        assertEq(address(rejecter).balance, 10 ether, "refunded once it accepts");
        assertEq(app.balance, 0);
    }

    // F7: accounts-drive root proof: one-shot, against the last finalized hash, wrong position fails. APP:217-255.
    function testF7_proveAccountsDriveMerkleRootOneShot() external {
        App memory a = _deploy(1);
        _resetMachine();
        _addAccount(LibUsdAccount.encode(ALICE, 100));
        LibEmulator.ProofComponents memory pc = _settle(a);
        bytes32 driveRoot = _accountsDriveRoot();
        bytes32[] memory proof = pc.getAccountsDriveMerkleRootProof();
        assertEq(proof.length, 64 - (5 + 17 + 0));
        assertEq(a.dc.getLastFinalizedMachineMerkleRoot(address(a.app)), pc.getMachineMerkleRoot());

        vm.expectRevert(IApplication.NotForeclosed.selector);
        a.app.proveAccountsDriveMerkleRoot(driveRoot, proof);
        _foreclose(a);

        // wrong size
        bytes32[] memory short_ = new bytes32[](proof.length - 1);
        vm.expectRevert(IApplication.InvalidAccountsDriveMerkleRootProofSize.selector);
        a.app.proveAccountsDriveMerkleRoot(driveRoot, short_);

        // siblings taken at a different drive position (one drive to the right)
        bytes32[] memory wrongPos = pc.getSiblings(LibEmulator.getAccountsDriveStartNodeIndex() + (uint64(1) << 17));
        bytes32[] memory wrongPosProof = new bytes32[](proof.length);
        for (uint256 i; i < proof.length; ++i) wrongPosProof[i] = wrongPos[17 + i];
        vm.expectRevert(); // InvalidMachineMerkleRoot(<recomputed root>)
        a.app.proveAccountsDriveMerkleRoot(driveRoot, wrongPosProof);

        // right proof, wrong root
        vm.expectRevert();
        a.app.proveAccountsDriveMerkleRoot(keccak256("other"), proof);

        a.app.proveAccountsDriveMerkleRoot(driveRoot, proof);
        (bool was, bytes32 proved) = a.app.getAccountsDriveMerkleRoot();
        assertTrue(was);
        assertEq(proved, driveRoot);
        vm.expectRevert(IApplication.AccountsDriveMerkleRootAlreadyProved.selector);
        a.app.proveAccountsDriveMerkleRoot(driveRoot, proof);
    }

    // F8: withdraw per account through UsdWithdrawalOutputBuilder. APP:257-281, UsdWithdrawalOutputBuilder:14-54.
    function testF8_withdrawPerAccountThroughUsdBuilder() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        IERC20 usd = IERC20(address(_contracts.dev.testUsdc));
        _contracts.dev.testUsdc.mint(app, 1_000_000);

        _resetMachine();
        uint64 iAlice = _addAccount(LibUsdAccount.encode(ALICE, 300_000));
        bytes memory bob31 = new bytes(31); // ill-sized account present in the drive
        bob31[0] = 0x01;
        uint64 iBob31 = _addAccount(bob31);
        uint64 iCarol = _addAccount(LibUsdAccount.encode(address(0xCA201), 900_000)); // more than the pool holds
        LibEmulator.ProofComponents memory pc = _settle(a);

        _foreclose(a);
        a.app.proveAccountsDriveMerkleRoot(_accountsDriveRoot(), pc.getAccountsDriveMerkleRootProof());

        AccountValidityProof memory pa = _accountProof(iAlice);
        AccountValidityProof memory pb = _accountProof(iBob31);
        AccountValidityProof memory pcarol = _accountProof(iCarol);

        a.app.withdraw(LibUsdAccount.encode(ALICE, 300_000), pa);
        assertEq(usd.balanceOf(ALICE), 300_000, "uint96 balance moved by SafeErc20Transfer");
        assertTrue(a.app.wereAccountFundsWithdrawn(iAlice));

        vm.expectRevert(abi.encodeWithSelector(IApplication.AccountFundsAlreadyWithdrawn.selector, iAlice));
        a.app.withdraw(LibUsdAccount.encode(ALICE, 300_000), pa);

        // a tampered balance fails the drive proof, not the builder
        vm.expectRevert();
        a.app.withdraw(LibUsdAccount.encode(ALICE, 300_001), pa);

        vm.expectRevert(abi.encodeWithSelector(IWithdrawalOutputBuilderErrors.InvalidAccountSize.selector, 31, 32));
        a.app.withdraw(bob31, pb);

        // the builder only knows USD: an account whose balance is not backed by USD in the pool cannot exit
        vm.expectRevert(
            abi.encodeWithSelector(IERC20Errors.ERC20InsufficientBalance.selector, app, 700_000, 900_000)
        );
        a.app.withdraw(LibUsdAccount.encode(address(0xCA201), 900_000), pcarol);
        assertFalse(a.app.wereAccountFundsWithdrawn(iCarol), "stays withdrawable");
        emit log_string("F8 observed: non-USD assets are unreachable through withdraw with UsdWithdrawalOutputBuilder (document)");
    }

    // F9: zero WithdrawalConfig deploys fine and means 'no emergency exit at all'. LibWithdrawalConfig:10-34.
    function testF9_zeroWithdrawalConfigMeansNoExit() external {
        App memory a = _deploy(1000, _noSentries(), _zeroWc(), bytes32(uint256(0xF9)));
        WithdrawalConfig memory wc = a.app.getWithdrawalConfig();
        assertEq(wc.guardian, address(0));
        assertEq(address(wc.withdrawalOutputBuilder), address(0));
        assertEq(a.app.getGuardian(), address(0));

        // foreclose: uncallable by any real sender (msg.sender == 0 cannot sign)
        vm.prank(address(0x1234));
        vm.expectRevert(IApplication.NotGuardian.selector);
        a.app.foreclose();
        vm.prank(GUARDIAN);
        vm.expectRevert(IApplication.NotGuardian.selector);
        a.app.foreclose();
        // every exit function is gated on foreclosure, so none is reachable
        bytes32[] memory p59 = new bytes32[](59);
        vm.expectRevert(IApplication.NotForeclosed.selector);
        a.app.proveAccountsDriveMerkleRoot(bytes32(0), p59);
        vm.expectRevert(IApplication.NotForeclosed.selector);
        a.app.issueRefund(0, "");
        AccountValidityProof memory ap;
        vm.expectRevert(IApplication.NotForeclosed.selector);
        a.app.withdraw(new bytes(32), ap);

        // Hypothetical: even if foreclosure happened (impossible on chain; forged with a prank from address(0)),
        // withdraw would fail because the builder is address(0).
        uint256 snap = vm.snapshotState();
        vm.prank(address(0));
        a.app.foreclose();
        ap.accountRootSiblings = new bytes32[](0);
        vm.expectRevert(); // AccountsDriveMerkleRootNotProved / staticcall to address(0) decode failure
        a.app.withdraw(new bytes(32), ap);
        vm.revertToState(snap);
        emit log_string("F9 observed: zero config = isValid passes, guardian 0 => foreclose uncallable => no refund/withdraw path ever");
    }

    // F10: double exit after foreclosure: a user with a drive balance AND an executable voucher. APP:163-184, 257-281.
    // Determined: both pay from the same pool with no cross-check; the user is paid twice while the pool lasts,
    // at the expense of other account holders. (B9's severity: P0-reachable only after a guardian foreclosure.)
    function testF10_doubleExitVoucherPlusWithdrawPaysTwiceFromSharedPool() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        IERC20 usd = IERC20(address(_contracts.dev.testUsdc));
        _contracts.dev.testUsdc.mint(app, 200); // Alice's 100 + Bob's 100 in the pool

        // settled state: Alice holds 100 in the drive AND the machine emitted a 100 USD voucher to Alice
        _resetMachine();
        uint64 iAlice = _addAccount(LibUsdAccount.encode(ALICE, 100));
        uint64 iBob = _addAccount(LibUsdAccount.encode(BOB, 100));
        bytes memory voucher = _delegateCallVoucher(
            address(_contracts.core.safeErc20Transfer), abi.encodeCall(ISafeErc20Transfer.safeTransfer, (usd, ALICE, 100))
        );
        uint64 iV = _addOutput(voucher);
        LibEmulator.ProofComponents memory pc = _settle(a);
        OutputValidityProof memory pv = _outputProof(iV);
        AccountValidityProof memory paAlice = _accountProof(iAlice);
        AccountValidityProof memory paBob = _accountProof(iBob);

        _foreclose(a);
        a.app.proveAccountsDriveMerkleRoot(_accountsDriveRoot(), pc.getAccountsDriveMerkleRootProof());

        a.app.executeOutput(voucher, pv); // exit 1: the voucher
        assertEq(usd.balanceOf(ALICE), 100);
        a.app.withdraw(LibUsdAccount.encode(ALICE, 100), paAlice); // exit 2: the drive balance
        assertEq(usd.balanceOf(ALICE), 200, "Alice paid twice for one balance");
        assertEq(usd.balanceOf(app), 0);

        vm.expectRevert(abi.encodeWithSelector(IERC20Errors.ERC20InsufficientBalance.selector, app, 0, 100));
        a.app.withdraw(LibUsdAccount.encode(BOB, 100), paBob);
        emit log_string("F10 observed: executeOutput + withdraw both paid; no per-user cross-check; Bob's exit fails for lack of pool");
    }

    // F11: after foreclose(), portal deposits revert as a whole; assets are never stranded. InputBox:23.
    function testF11_foreclosureStopsDeposits() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        vm.deal(ALICE, 10 ether);
        _contracts.dev.testFungibleToken.mint(ALICE, 100);
        vm.prank(ALICE);
        _contracts.dev.testFungibleToken.approve(address(_contracts.core.erc20Portal), 100);
        _foreclose(a);

        vm.prank(ALICE);
        vm.expectRevert(_encodeApplicationForeclosed(app));
        _contracts.core.etherPortal.depositEther{value: 1 ether}(app, "");
        vm.prank(ALICE);
        vm.expectRevert(_encodeApplicationForeclosed(app));
        _contracts.core.erc20Portal.depositErc20Tokens(_contracts.dev.testFungibleToken, app, 100, "");
        vm.expectRevert(_encodeApplicationForeclosed(app));
        _contracts.core.inputBox.addInput(app, "x");

        assertEq(ALICE.balance, 10 ether, "ether rolled back");
        assertEq(_contracts.dev.testFungibleToken.balanceOf(ALICE), 100, "tokens rolled back");
        assertEq(app.balance, 0);
        assertEq(_contracts.core.inputBox.getNumberOfInputs(app), 0);
    }

    // F12: sentry manager 0x0 and a compromised sentry. DC:283-296, 464-466.
    function testF12_zeroSentryManagerAndCompromisedSentry() external {
        address[] memory sentries = new address[](1);
        sentries[0] = address(0x51);
        uint256 period = 10;
        App memory a;
        a.tf = new StubTournamentFactory();
        a.factory = new DaveAppFactory(_contracts.core.inputBox, _contracts.core.applicationFactory, a.tf);
        (a.app, a.dc) = a.factory.newDaveApp(TEMPLATE, period, address(0), sentries, _wc(), bytes32(uint256(0xF12)));
        assertEq(a.dc.getSentryManager(), address(0));

        // no rotation by anyone
        vm.prank(SENTRY_MANAGER);
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.CallerIsNotSentryManager.selector, SENTRY_MANAGER));
        a.dc.rotateSentry(sentries[0], address(0x99));
        vm.prank(sentries[0]);
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.CallerIsNotSentryManager.selector, sentries[0]));
        a.dc.rotateSentry(sentries[0], address(0x99));

        // compromised sentry claims a wrong hash: it can neither block nor corrupt acceptance
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        _stage(a, pc);
        Machine.Hash wrong = Machine.Hash.wrap(keccak256("wrong"));
        Machine.Hash right = Machine.Hash.wrap(pc.getMachineMerkleRoot()); // external lib call: before prank/expectRevert
        vm.prank(sentries[0]);
        a.dc.submitSentryClaim(0, wrong);
        vm.prank(sentries[0]);
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.SentryAlreadyClaimed.selector, 0, 1));
        a.dc.submitSentryClaim(0, right);
        (, bool allAgree,,,,) = a.dc.canAcceptStagedTournamentResult();
        assertFalse(allAgree);
        vm.expectRevert(abi.encodeWithSelector(IDaveConsensus.ClaimStagingPeriodNotOverYet.selector, 0, period));
        a.dc.acceptStagedTournamentResult(0);
        vm.roll(block.number + period);
        a.dc.acceptStagedTournamentResult(0);
        assertEq(_epoch(a), 1, "accepted after the period despite the dissenting claim");
        assertEq(a.dc.getLastFinalizedMachineMerkleRoot(address(a.app)), pc.getMachineMerkleRoot(), "staged hash won, not the claim");

        // a sentry can only shorten: agreeing claim skips the period
        _resetMachine();
        _addOutput(_notice("e1"));
        LibEmulator.ProofComponents memory pc1 = _pc();
        _stage(a, pc1);
        Machine.Hash right1 = Machine.Hash.wrap(pc1.getMachineMerkleRoot());
        vm.prank(sentries[0]);
        a.dc.submitSentryClaim(1, right1);
        a.dc.acceptStagedTournamentResult(1);
        assertEq(_epoch(a), 2);
    }
}
