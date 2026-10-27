// SPDX-License-Identifier: Apache-2.0
// Funds-safety plan, Area J: deployment and configuration (P1).
pragma solidity ^0.8.30;

import {WithdrawalConfig} from "cartesi-rollups-contracts-3.0.0/src/common/WithdrawalConfig.sol";
import {IApplication} from "cartesi-rollups-contracts-3.0.0/src/dapp/IApplication.sol";
import {IWithdrawalOutputBuilder} from "cartesi-rollups-contracts-3.0.0/src/withdrawal/IWithdrawalOutputBuilder.sol";
import {LibEmulator} from "cartesi-rollups-contracts-3.0.0/test/util/LibEmulator.sol";

import {DaveAppFactory} from "src/DaveAppFactory.sol";
import {IDaveConsensus} from "src/IDaveConsensus.sol";

import {FundsSafetyBase, StubTournamentFactory} from "./FundsSafetyBase.sol";

contract FundsSafetyJTest is FundsSafetyBase {
    using LibEmulator for LibEmulator.State;

    // J2: the release-fixture / echo-app configuration is accepted by the factory. DaveAppFactory:34-48.
    // Prints the resulting guardian window and exit availability as a deployment checklist.
    function testJ2_releaseFixtureConfigurationIsAcceptedSilently() external {
        App memory a = _deploy(0, _noSentries(), _zeroWc(), bytes32(uint256(0x12)));

        uint256 period = a.dc.getClaimStagingPeriod();
        uint256 nSentries = a.dc.getNumberOfSentries();
        WithdrawalConfig memory wc = a.app.getWithdrawalConfig();
        bool exitAvailable = wc.guardian != address(0) && address(wc.withdrawalOutputBuilder) != address(0);

        emit log_string("J2 deployment checklist (echo app / release fixtures)");
        emit log_named_uint("  claimStagingPeriod (blocks)", period);
        emit log_named_uint("  number of sentries", nSentries);
        emit log_named_uint("  guardian window after staging (blocks)", period);
        emit log_named_address("  guardian", wc.guardian);
        emit log_named_address("  withdrawal builder", address(wc.withdrawalOutputBuilder));
        emit log_named_string("  emergency exit available", exitAvailable ? "yes" : "NO");

        assertEq(period, 0);
        assertEq(nSentries, 0);
        assertFalse(exitAvailable);

        // the consequence end to end: a staged result is final in its own block and nobody can ever foreclose
        _resetMachine();
        LibEmulator.ProofComponents memory pc = _pc();
        _stage(a, pc);
        a.dc.acceptStagedTournamentResult(0);
        assertEq(_epoch(a), 1, "accepted in the staging block");
        vm.prank(GUARDIAN);
        vm.expectRevert(IApplication.NotGuardian.selector);
        a.app.foreclose();
    }

    // J3: address squatting of the predicted app address for a salt (CTR-1). ApplicationFactory:54-78.
    function testJ3_squattedPredictedAddressMakesDeployRevert() external {
        StubTournamentFactory tf = new StubTournamentFactory();
        DaveAppFactory f = new DaveAppFactory(_contracts.core.inputBox, _contracts.core.applicationFactory, tf);
        bytes32 salt = bytes32(uint256(0xC7B1));
        (address predictedApp, address predictedDc) =
            f.calculateDaveAppAddress(TEMPLATE, 1000, SENTRY_MANAGER, _noSentries(), _wc(), salt);
        assertEq(predictedApp.code.length, 0);

        // (a) anything with code at the predicted app address blocks that salt for good
        uint256 snap = vm.snapshotState();
        vm.etch(predictedApp, hex"00");
        // A CREATE2 collision burns all gas handed to the creation frame, so bound the call's gas.
        (bool ok, bytes memory ret) = address(f).call{gas: 5_000_000}(
            abi.encodeCall(f.newDaveApp, (TEMPLATE, 1000, SENTRY_MANAGER, _noSentries(), _wc(), salt))
        );
        assertFalse(ok, "deploy for the squatted salt reverts");
        assertEq(ret.length, 0, "empty revert data (CREATE2 collision)");
        assertEq(predictedDc.code.length, 0, "nothing deployed");
        vm.revertToState(snap);

        // (b) the realistic squat: anyone front-runs the identical newDaveApp call. The second call reverts,
        // but the app that exists is byte-identical (owner renounced, validator = its consensus), so no funds are at risk.
        vm.prank(address(0xF207));
        (IApplication app, IDaveConsensus dc) = f.newDaveApp(TEMPLATE, 1000, SENTRY_MANAGER, _noSentries(), _wc(), salt);
        assertEq(address(app), predictedApp);
        assertEq(address(dc), predictedDc);
        (ok,) = address(f).call{gas: 5_000_000}(
            abi.encodeCall(f.newDaveApp, (TEMPLATE, 1000, SENTRY_MANAGER, _noSentries(), _wc(), salt))
        );
        assertFalse(ok, "second identical deploy reverts");
        assertEq(address(app.getOutputsMerkleRootValidator()), predictedDc);
        assertEq(dc.getApplicationContract(), predictedApp);

        // (c) a different salt still works for the original deployer: griefing only, no loss
        (IApplication app2,) = f.newDaveApp(TEMPLATE, 1000, SENTRY_MANAGER, _noSentries(), _wc(), bytes32(uint256(0xC7B2)));
        assertTrue(address(app2) != predictedApp);
        emit log_string("J3 observed: squatted salt reverts the deploy; identical front-run yields the same app; griefing only");
    }
}
