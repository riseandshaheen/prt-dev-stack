// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area G: deposits and input integrity (P2).
pragma solidity ^0.8.30;

import {IERC20} from "@openzeppelin-contracts-5.2.0/token/ERC20/IERC20.sol";
import {IERC721} from "@openzeppelin-contracts-5.2.0/token/ERC721/IERC721.sol";
import {IERC1155} from "@openzeppelin-contracts-5.2.0/token/ERC1155/IERC1155.sol";

import {Inputs} from "cartesi-rollups-contracts-3.0.0/src/common/Inputs.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {IInputBox} from "cartesi-rollups-contracts-3.0.0/src/inputs/IInputBox.sol";
import {IErc20Portal} from "cartesi-rollups-contracts-3.0.0/src/portals/IErc20Portal.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";

import {FundsSafetyBase} from "./FundsSafetyBase.sol";

/// @dev ERC-20 that keeps a 1% fee on every transferFrom (fee-on-transfer semantics).
contract FeeOnTransferToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 v) external {
        balanceOf[to] += v;
    }

    function approve(address s, uint256 v) external returns (bool) {
        allowance[msg.sender][s] = v;
        return true;
    }

    function transferFrom(address from, address to, uint256 v) external returns (bool) {
        allowance[from][msg.sender] -= v;
        balanceOf[from] -= v;
        uint256 fee = v / 100;
        balanceOf[to] += v - fee;
        balanceOf[address(0xFEE)] += fee;
        return true;
    }
}

/// @dev "Rebasing": the app's balance is reported larger than what was transferred.
contract RebasingToken {
    mapping(address => uint256) public raw;
    mapping(address => mapping(address => uint256)) public allowance;
    uint256 public multiplier = 2;

    function mint(address to, uint256 v) external {
        raw[to] += v;
    }

    function balanceOf(address who) external view returns (uint256) {
        return raw[who] * multiplier;
    }

    function approve(address s, uint256 v) external returns (bool) {
        allowance[msg.sender][s] = v;
        return true;
    }

    function transferFrom(address from, address to, uint256 v) external returns (bool) {
        allowance[from][msg.sender] -= v;
        raw[from] -= v;
        raw[to] += v;
        return true;
    }
}

contract AppGetInputBoxReverts {
    error Nope();

    receive() external payable {}

    function getInputBox() external pure returns (address) {
        revert Nope();
    }
}

contract AppGetInputBoxReturnsEoa {
    address public immutable EOA;

    constructor(address eoa) {
        EOA = eoa;
    }

    receive() external payable {}

    function getInputBox() external view returns (address) {
        return EOA;
    }
}

contract FundsSafetyGTest is FundsSafetyBase {
    using LibEmulator for LibEmulator.State;

    address internal constant ALICE = address(0xA11);

    // G1: assets sent directly are held with no input and no refund path. APP:36-37, 160.
    function testG1_directTransfersAreHeldWithoutInputOrRefund() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        vm.deal(ALICE, 10 ether);
        _contracts.dev.testFungibleToken.mint(ALICE, 100);
        _contracts.dev.testNonFungibleToken.mint(ALICE, 7);
        _contracts.dev.testMultiToken.mint(ALICE, 11, 5);

        vm.startPrank(ALICE);
        (bool ok,) = app.call{value: 1 ether}("");
        assertTrue(ok, "receive() accepts bare Ether");
        _contracts.dev.testFungibleToken.transfer(app, 100);
        IERC721(address(_contracts.dev.testNonFungibleToken)).safeTransferFrom(ALICE, app, 7);
        IERC1155(address(_contracts.dev.testMultiToken)).safeTransferFrom(ALICE, app, 11, 5, "");
        vm.stopPrank();

        assertEq(app.balance, 1 ether);
        assertEq(_contracts.dev.testFungibleToken.balanceOf(app), 100);
        assertEq(_contracts.dev.testNonFungibleToken.ownerOf(7), app);
        assertEq(_contracts.dev.testMultiToken.balanceOf(app, 11), 5);
        assertEq(_contracts.core.inputBox.getNumberOfInputs(app), 0, "no input recorded for any of them");

        // after foreclosure nothing can be refunded: there is no input index to refund
        vm.prank(GUARDIAN);
        a.app.foreclose();
        vm.expectRevert(abi.encodeWithSelector(IApplication.InvalidInputIndex.selector, 0, 0));
        a.app.issueRefund(0, "");
        emit log_string("G1 observed: receive()/holder hooks/plain transfer are held; machine never sees them; no refund path (document)");
    }

    // G2: fee-on-transfer and rebasing tokens are rejected by the balance-delta check. Erc20Portal:31-41.
    function testG2_feeOnTransferAndRebasingTokensRejected() external {
        App memory a = _deploy(1);
        address app = address(a.app);

        FeeOnTransferToken fee = new FeeOnTransferToken();
        fee.mint(ALICE, 1000);
        vm.startPrank(ALICE);
        fee.approve(address(_contracts.core.erc20Portal), 1000);
        vm.expectRevert(abi.encodeWithSelector(IErc20Portal.Erc20TransferValueIsNotBalanceDelta.selector, 1000, 990));
        _contracts.core.erc20Portal.depositErc20Tokens(IERC20(address(fee)), app, 1000, "");
        vm.stopPrank();
        assertEq(fee.balanceOf(ALICE), 1000, "transfer rolled back");

        RebasingToken reb = new RebasingToken();
        reb.mint(ALICE, 1000);
        vm.startPrank(ALICE);
        reb.approve(address(_contracts.core.erc20Portal), 1000);
        vm.expectRevert(abi.encodeWithSelector(IErc20Portal.Erc20TransferValueIsNotBalanceDelta.selector, 1000, 2000));
        _contracts.core.erc20Portal.depositErc20Tokens(IERC20(address(reb)), app, 1000, "");
        vm.stopPrank();
        assertEq(reb.raw(ALICE), 1000, "transfer rolled back");
        assertEq(_contracts.core.inputBox.getNumberOfInputs(app), 0, "no deposit recorded for a wrong amount");
    }

    // G3: portal pointed at an app whose getInputBox reverts or names a non-contract. Portal:46-92.
    function testG3_portalRejectsBrokenApps() external {
        AppGetInputBoxReverts bad = new AppGetInputBoxReverts();
        AppGetInputBoxReturnsEoa eoaBox = new AppGetInputBoxReturnsEoa(address(0x1234));
        vm.deal(ALICE, 10 ether);
        _contracts.dev.testFungibleToken.mint(ALICE, 100);
        vm.prank(ALICE);
        _contracts.dev.testFungibleToken.approve(address(_contracts.core.erc20Portal), 100);

        vm.prank(ALICE);
        vm.expectRevert(_encodeApplicationReverted(address(bad), abi.encodeWithSelector(AppGetInputBoxReverts.Nope.selector)));
        _contracts.core.etherPortal.depositEther{value: 1 ether}(address(bad), "");
        vm.prank(ALICE);
        vm.expectRevert(_encodeInputBoxNotDeployed(address(0x1234)));
        _contracts.core.etherPortal.depositEther{value: 1 ether}(address(eoaBox), "");
        vm.prank(ALICE);
        vm.expectRevert(_encodeApplicationReverted(address(bad), abi.encodeWithSelector(AppGetInputBoxReverts.Nope.selector)));
        _contracts.core.erc20Portal.depositErc20Tokens(_contracts.dev.testFungibleToken, address(bad), 100, "");
        vm.prank(ALICE);
        vm.expectRevert(_encodeInputBoxNotDeployed(address(0x1234)));
        _contracts.core.erc20Portal.depositErc20Tokens(_contracts.dev.testFungibleToken, address(eoaBox), 100, "");
        vm.prank(ALICE);
        vm.expectRevert(_encodeApplicationNotDeployed(address(0x5555)));
        _contracts.core.etherPortal.depositEther{value: 1 ether}(address(0x5555), "");

        assertEq(ALICE.balance, 10 ether, "ether rolled back");
        assertEq(address(bad).balance, 0);
        assertEq(address(eoaBox).balance, 0);
        assertEq(_contracts.dev.testFungibleToken.balanceOf(ALICE), 100, "tokens rolled back");
    }

    // G4: input size boundary. The cap is on the whole encoded EvmAdvance blob. InputBox:44-48.
    function testG4_inputSizeBoundary() external {
        App memory a = _deploy(1);
        address app = address(a.app);
        // blob = 4 (selector) + 8*32 (head) + 32 (payload length) + pad32(len) = 292 + pad32(len)
        // 292 = 4 mod 32 and 65536 = 0 mod 32, so no payload yields exactly 64 KiB. Largest accepted: 65508.
        uint256 maxLen = 65216; // pad32 = 65216 -> blob 65508
        bytes memory biggest = new bytes(maxLen);
        bytes memory oneOver = new bytes(maxLen + 1); // pad32 = 65248 -> blob 65540

        uint256 encBig = abi.encodeCall(
            Inputs.EvmAdvance, (block.chainid, app, address(this), block.number, block.timestamp, block.prevrandao, 0, biggest)
        ).length;
        uint256 encOver = abi.encodeCall(
            Inputs.EvmAdvance, (block.chainid, app, address(this), block.number, block.timestamp, block.prevrandao, 0, oneOver)
        ).length;
        emit log_named_uint("G4 largest accepted blob bytes", encBig);
        emit log_named_uint("G4 one payload byte more -> blob bytes", encOver);
        assertEq(encBig, 65508);
        assertEq(encOver, 65540);
        assertTrue(encBig < 65536 && encOver > 65536, "exactly 65536 is unreachable: blob length is 4 mod 32");

        _contracts.core.inputBox.addInput(app, biggest);
        assertEq(_contracts.core.inputBox.getNumberOfInputs(app), 1);
        vm.expectRevert(abi.encodeWithSelector(IInputBox.InputTooLarge.selector, app, encOver, 65536));
        _contracts.core.inputBox.addInput(app, oneOver);
        assertEq(_contracts.core.inputBox.getNumberOfInputs(app), 1);
    }
}
