## Problem Statement

The engine's shared pre-dispatch guard refuses to act when the session has no usable interactive desktop, and it returns the error `desktop_inaccessible`. Every input-dispatching action composes that check. It is the only one of the eight guard checks with no test at all, and it is the only one whose condition cannot be provoked on the machine the suite runs on.

This is worse than an ordinary coverage gap, because the gap has been described as "not attempted", which reads as though it were merely inconvenient. It is not. The check cannot be exercised by any means that is safe to run against a live interactive session, and establishing that required measuring three separate approaches that all fail. The reasons are not obvious from the code and cost real time to rediscover:

- `OpenInputDesktop` reports the input desktop of the **window station**, not of the calling thread. A thread that has called `SetThreadDesktop` onto a private desktop still reads `Default` from it. Measured directly: thread attached to `WcuProbeDesk2` reports input desktop `Default`. Any approach built on per-thread desktop attachment is therefore a no-op, and this also explains why launching the engine and fixture onto a private desktop changes nothing, since the engine additionally re-attaches itself to the input desktop during startup.
- `CreateWindowStation`, the only way to obtain a window station whose input desktop is genuinely not `Default`, fails with `ERROR_ACCESS_DENIED` because it requires `SE_CREATE_WINDOW_STATION`, which is not granted to an interactive user. Measured: four different access masks, all err 5.
- The two remaining routes both change the state of the user's own desktop. Locking the session destroys the interactive desktop the suite is running in. `SwitchDesktop` blanks everything currently displayed. Neither is acceptable for a test suite.

The consequence is a guard that guards nothing that has been demonstrated. The refusal path is written, wired into six actions, and has never once been observed to fire. If the check were deleted, or its error code renamed, or its call sites dropped from one action during a refactor, no test would fail. Meanwhile the check's error text tells an operator that the session is locked or headless, which is a claim about the machine that nothing in the repository has ever confirmed the engine can actually make.

## Solution

Cover the decision that can be covered at the seam where it is decidable, cover the Win32 call itself against the live operating system, and give the integration suite a narrow, one-way way to observe the refusal end to end.

Split the desktop check into two parts. The first part performs the Win32 calls and reduces them to facts: could the input desktop be opened, and what is it called. The second part is a pure verdict over those facts. The pure verdict is where the actual decision lives, and it can be tested directly against the names Windows actually uses. The Win32 part is tested against the live API for the branch that a live interactive session can honestly produce.

Because the refusal condition cannot be created, the integration suite gets an explicit test valve: an environment variable that can only ever force the inaccessible verdict. It cannot make a failing check pass, so it cannot be used to weaken the guard even if it leaks into a real environment. A leaked valve makes the engine refuse everything, which is a loud failure rather than a silent one. With that valve the suite can prove the property the product actually cares about: that all six input-dispatching actions refuse, return `desktop_inaccessible`, and dispatch nothing at all, verified by reading the application's own state rather than the engine's return value.

Record precisely what remains uncovered, so the residual risk is named instead of assumed away.

## User Stories

1. As a user whose desktop session has locked or become disconnected, I want every action refused, so that no keystroke or click is dispatched into a desktop that will not display it and cannot be undone.
2. As a user running the engine in a service or non-interactive window station, I want the engine to refuse rather than inject input, so that a misconfigured deployment fails closed instead of driving an invisible desktop.
3. As a user, I want the refusal to be distinguishable from other refusals, so that I can tell "the session is not interactive" apart from "the window moved" or "the target is occluded" and act on it.
4. As a user, I want the refusal to name the desktop that was actually found, so that a desktop which is interactive but unexpectedly named can be diagnosed without a debugger.
5. As a user, I want the message to distinguish a real condition from a test artifact, so that a refusal produced by the test valve is never mistaken for a genuine locked session.
6. As a user, I want the refusal to happen before any input is dispatched rather than after, so that a partially completed action cannot occur.
7. As a user of the click action, I want it refused when the session is not interactive, so that the original guarded click keeps the same protections it has always had.
8. As a user of the typing action, I want it refused when the session is not interactive, so that text is never injected into an unattended desktop.
9. As a user of the key chord action, I want it refused when the session is not interactive, so that shortcuts cannot fire where they cannot be seen.
10. As a user of the scroll action, I want it refused when the session is not interactive, so that scrolling a background window cannot move what the user is looking at.
11. As a user of the hover action, I want it refused when the session is not interactive, so that hovering cannot reveal menus or tooltips in a session nobody is watching.
12. As a user of the drag action, I want it refused when the session is not interactive, so that a drag cannot begin, because a gesture that begins and never completes leaves the mouse button logically held.
13. As a user, I want a gesture that is refused to have dispatched nothing, so that no button or modifier is left in a pressed state by an action that reported failure.
14. As a user, I want the refusal to be consistent across all actions, so that behaviour does not depend on which verb a caller happened to choose.
15. As a user, I want the check to run at the same point in the guard order as before, so that adding desktop coverage does not change which refusal a caller sees first for any other condition.
16. As a maintainer, I want the desktop check's decision logic tested directly, so that a change to the accepted desktop name is caught rather than discovered in production.
17. As a maintainer, I want the case-insensitivity of the accepted name preserved deliberately and tested, so that it is not accidentally removed by a well-meaning cleanup.
18. As a maintainer, I want the Win32 query itself exercised against the live operating system, so that a change in how the desktop is opened or named is detected on this machine and not only in review.
19. As a maintainer, I want the test valve to be incapable of making the guard pass, so that adding test support cannot become a way to bypass a safety check.
20. As a maintainer, I want the test valve documented where a reader of the guard will find it, so that nobody discovers it by reading the environment variable's name.
21. As a maintainer, I want the test valve's residual risk recorded on the limitation board, so that its existence is auditable rather than folklore.
22. As a maintainer, I want to know which branch of the check is still unverified and why, so that the coverage claim I make is the one I can actually defend.
23. As a reviewer, I want the reason the refusal cannot be provoked for real stated with the measurements that establish it, so that I do not re-investigate four dead ends.
24. As a reviewer, I want the seam chosen to be the highest one that can actually reach the behaviour, so that the tests prove the product property rather than an internal detail.
25. As a user, I want the engine to keep working normally when the valve is not set, so that this change is invisible in ordinary use.
26. As a user, I want the existing forty tests to keep passing unchanged, so that the guard refactor that introduced the check is still protected against regression.
27. As a user, I want the window focus action to keep its current behaviour, so that adding coverage does not silently widen or narrow what focus is allowed to do.
28. As a maintainer, I want the focus action's exemption from this check to be explicit and justified, so that its absence from the six-action list reads as a decision rather than an oversight.
29. As a maintainer, I want the check's error code to remain stable, so that callers and tests written against it do not break.
30. As a maintainer, I want the error message to remain useful when the real desktop cannot be queried at all, since that is the branch that fires in a genuinely locked session.

## Implementation Decisions

**The check is split into a fact-gathering part and a pure verdict, and the verdict is what gets unit-tested.** The Win32 part answers two questions and nothing else: could the input desktop be opened, and what is its name. The verdict takes those two facts and returns the decision. This is the only seam at which the decision can be tested without a condition that cannot be created, and it is a seam that already exists in spirit, because the current function computes exactly this verdict inline.

**The accepted desktop name stays case-insensitive, and the trim decision is made explicitly.** Windows returns `Default` in normal operation. The current code compares case-insensitively against `Default`; that behaviour is kept. A name that is empty, or that differs only by surrounding whitespace, is refused rather than trimmed, on the principle that a name the engine did not expect exactly is a reason to stop and report rather than a reason to guess. This is a deliberate tightening of an implicit behaviour, made at the moment the behaviour becomes testable, and it is recorded here because it could reject a desktop name that the previous code would have accepted.

**The verdict distinguishes the two failure shapes in its message, because they mean different things to an operator.** "The input desktop could not be opened" points at a locked or disconnected session. "The input desktop is named something other than Default" points at a non-interactive window station or an unexpectedly configured desktop. Both produce the same protocol error code, because the caller's remedy is the same, but the message must not conflate them.

**The test valve is an environment variable that can only force the inaccessible verdict.** When it is set, the desktop check returns the inaccessible result immediately, with a message that says the refusal came from the valve. It is checked as a one-way gate: there is no value of this variable, and no code path, that causes a failing check to return success. This asymmetry is the whole reason it is acceptable to add, and it is what distinguishes it from a general override.

**The valve is honoured in release builds as well as debug builds.** A valve restricted to debug builds would silently stop working for anyone who runs the suite against a release binary, and the resulting tests would pass while asserting nothing. Failing closed in production is the preferable failure mode to a test that has quietly stopped testing.

**The valve is read once per check, from the process environment, and is not cached.** The engine is a long-lived process, and a valve that is read once at startup could not be used to toggle the condition within a single test session. Reading it per call is cheap relative to the desktop handle it opens.

**The valve's name is explicit that it is a test control, not a feature.** It carries a prefix marking it as a test hook, so that it does not read as a supported deployment option.

**The focus action keeps its current exemption.** `focus_window` composes no guard check today, and this change does not add one. The reason is that it dispatches no input: it changes which window is foreground and then re-observes. Refusing it on desktop state would block a caller from learning which window is present, and the risk it would guard against, input landing somewhere unintended, cannot arise from a focus request alone. This is recorded as a decision so that the six-action list reads as deliberate.

**The protocol error code does not change.** `desktop_inaccessible` remains the code, because it is already the contract callers and existing tests are written against.

**No change to guard ordering.** The check keeps its current position, third for pointer actions and first for keyboard actions, so that no other refusal changes priority as a side effect.

**The engine does not gain a protocol operation for this.** The valve is an environment-driven test control, not a request the caller can send. Adding a request that could report a desktop as inaccessible would mean any caller, including a model-driven one, could talk the engine into refusing, and a more serious mistake could conceivably be talked into accepting.

## Testing Decisions

**A good test here asserts externally observable behaviour.** What matters is that an action refuses and that nothing is dispatched, not that an internal function was called. Every integration test therefore reads the application's own reported state before and after, and asserts both the error code and that the state did not move. Asserting only the error code would pass even if the engine had already typed the text and then reported failure.

**The existing fixture is the observation point, and it is not modified.** It already records what it receives. Refusing to add a new control or a new recording channel keeps the test observing the same application that the rest of the suite observes, so a bug that broke ordinary typing would break these tests too.

**The three targeted Rust unit tests, in the existing style, in the module they cover.** There is already prior art for this: in-tree test modules alongside the code they cover, run with `cargo test`, rather than a separate integration-test crate. The tests cover the pure verdict against the names Windows actually uses, including the interactive name, a differently-cased interactive name, the names Windows uses for the logon and screen-saver desktops, a non-interactive name, an empty name, and a name with surrounding whitespace. They also cover the live Win32 query's passing branch, asserting that on this machine it succeeds and reports the interactive desktop, which is the only branch an interactive session can honestly produce and which exercises the real API usage. Finally they cover the valve: with it set the verdict is inaccessible even though the desktop is genuinely interactive, and with it unset the verdict is interactive, together establishing that the valve only ever moves the result in the refusing direction.

**Testing the live query from a unit test is a deliberate second branch, not a substitute.** It cannot observe a failure, and it does not pretend to. Its job is to catch a change in how the desktop is opened, queried, or named, which a pure-function test would not notice.

**The integration tests cover all six input-dispatching actions, not a sample.** Click, typing, key chords, scroll, hover and drag each get a test, because the failure this guards against is one action losing its composition during a refactor, and a sample cannot detect that. The focus action is deliberately not in the list, for the reason recorded above.

**Each integration test asserts two things, and both are required.** That the action returns `desktop_inaccessible`, and that the fixture's own reported state is unchanged afterwards. The second assertion is the one that matters: it is what turns "the engine said no" into "the engine did nothing".

**The engine under these tests is launched with the valve set, and a control case runs first without it.** Without the control, a suite in which the valve were somehow always active would pass every test while proving nothing. The control establishes that the same engine, the same fixture and the same assertions pass through when the condition is not forced.

**The existing forty tests and the twenty-seven phase seven tests must pass unchanged.** They are the regression oracle for the guard, and a change that quietly altered guard ordering or the accepted desktop name would show up there first.

**Every approach that was tried and failed is recorded as a limitation, with the measurement that killed it.** A future reader who believes the refusal is testable should not have to spend a day rediscovering that it is not, and should not have to rediscover it by attempting things that disrupt the machine.

## Delivery Status

Delivered 30 September 2026, tracked as [issue #5](https://github.com/enkay01/enkay-skills-workshop/issues/5). The Python suite is 77 tests, up from 67, and the engine gains 8 unit tests, bringing `cargo test` to 10.

**Delivered as specified.** The Win32 query now reduces to what it found, and the decision is a pure verdict over that, tested directly against the names Windows actually uses: case variants, `Winlogon`, `Screen-saver`, an empty name and a padded name. The live query's passing branch is exercised against the real API. The valve is honoured in every build. The error code, the guard ordering, the client and the focus action are all untouched.

**All six input-dispatching actions are proven to refuse and to dispatch nothing**, each asserted twice: the error code returned, and the application's own reported state re-read afterwards. Click, right click, typing, key chords, scrolling, hover and drag are covered, with a non-vacuous observable per action — a refused double click is caught by a click counter that would otherwise increment, a refused hover by the hover readout, a refused drag by the drop readout.

**The refusal is proven honest about itself.** This was user story 5, and it turned out to be the part most worth having: the message names the valve and says it is not a real condition, so nobody chases a locked session that does not exist.

**The focus exemption is pinned.** Window focus is still not gated by this check, and a test now asserts that it is not, so the exemption reads as a decision rather than an oversight.

**Sensitivity was verified rather than assumed.** With the valve's name deliberately misspelled, 8 of the 10 integration tests fail, and the 2 that still pass are the 2 that should not depend on it. A suite in which every action refused for an unrelated reason would therefore be caught rather than believed.

**One deliberate tightening landed with the tests.** An empty or padded desktop name is now refused rather than normalised. The previous inline comparison would also have refused these, so no behaviour changed in practice, but the decision is now explicit, commented, and covered — which is the point of making it reachable from a test.

**One thing was found that this specification did not anticipate.** Running the new suite on its own failed, and so does phase 7's own suite when run on its own: when no test has yet injected any input, no process is eligible to call `SetForegroundWindow`, and every action is refused with `foreground_changed`. The full suite passes only because earlier phases happen to inject input first. That ordering dependency pre-dates this work, but it would have made the new suite quietly unrunnable on its own, which is a bad property for a suite whose whole job is to be run and read. The new suite therefore claims foreground eligibility explicitly, with a zero-distance relative mouse movement — the same technique the action fixture already uses — so it does not depend on what runs before it. Phase 7 still carries the dependency, and removing it there would mean modifying the suite this project treats as its regression oracle.

**Still unverified, as anticipated.** The branch where the operating system refuses to hand over the input desktop at all, a genuinely locked session, is not exercised. It is the case the check most exists for, and closing it needs an isolated session or a separate host.

## Out of Scope

**Provoking a genuinely locked session, a disconnected session, or a headless desktop.** Each requires changing the state of the live interactive session the suite runs in. Recorded as unreachable rather than attempted.

**Session 0, service window stations, and any elevation.** Creating a window station fails without `SE_CREATE_WINDOW_STATION`. The suite does not elevate, and adding an elevation requirement to a test suite that currently needs none is a larger decision than this one.

**Switching the user's input desktop.** It blanks everything on screen and is not recoverable from within a test run.

**Changing what the focus action is allowed to do.** Its exemption from this check is recorded as a decision, not revisited here.

**The release-on-partial-input-failure path.** A separate and still-open correctness gap, unrelated to desktop state, tracked separately.

**Making the refusal condition reproducible for users.** This change makes the guard testable and documents it. It does not give anyone a way to reproduce a locked session on demand.

**Any change to the client beyond what the tests need.** The client stays transport-only.

## Further Notes

The finding worth carrying forward is that `OpenInputDesktop` is window-station scoped. The name suggests otherwise, the thread-attachment intuition is wrong, and this costs real time to establish because it produces a plausible-looking negative result: the handle you set, the desktop you land on, and the name the guard reads are three different things.

This also means the guard's name check is not the same check people assume. It verifies that the window station's input desktop is the interactive one, which is exactly right for detecting a non-interactive deployment, and it is the right thing to be checking. It is simply not a check that thread-level desktop control can influence.

The consequence for the coverage claim is worth stating plainly: after this change the desktop guard is verified on its decision logic, verified on the Win32 branch an interactive session can produce, and verified end to end for the refusal path by every action via a valve that can only make the engine refuse. The single branch that remains unverified is the operating system refusing to hand over the input desktop at all, which is the genuinely locked case. That is a real gap, it is narrow, and it is now named rather than implied.
