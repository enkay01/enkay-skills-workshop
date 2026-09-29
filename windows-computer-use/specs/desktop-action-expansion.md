## Problem Statement

The desktop tool can only click. A vision model that can see a window and dispatch a guarded left-click still cannot do the things real desktop tasks require: fill in a field, press a shortcut, read further down a page, hover a menu to reveal it, drag something somewhere, or move to a different application. The product roadmap lists these as the second delivery milestone, and the recorded model-controlled interaction names typing, scrolling, dragging, and application switching as the concrete gaps that keep the tool from being a general desktop agent rather than a click demo.

The user cannot yet hand a vision model a task that spans a form entry, a keyboard shortcut, a scroll, and a move into a second application. Every one of those steps stops at "unsupported operation". There is also a correctness risk in adding them naively: the existing guarded click derives its safety from revalidating window identity, observation freshness, geometry, foreground ownership, and hit-test ownership immediately before injecting input. Six new actions that each reimplement that preamble differently would make the tool less trustworthy than it is today, and would let input land in the wrong window.

## Solution

Extend the desktop action set beyond click, on the same engine and the same guarded contract, and prove each action against a real native window.

Add typing, key chords, scrolling, pointer hover, drag, and explicit window focus/switching. Each action reuses one shared pre-dispatch guard rather than a private copy, so an action is dispatched only when the window it was planned against is still the same live, unmoved, foreground, unobscured window. Every pointer action takes its target in the coordinate system of the observation the caller saw, never as a bare screen coordinate. Keyboard actions are bound to window identity and foreground, and may additionally be bound to an observation.

Window switching becomes an explicit, verifiable operation rather than a side effect. Focusing a window is confirmed against the actual foreground window, and a focus change invalidates the coordinates and observations from the previous window so a stale target cannot be reused after switching.

A second window in the controlled native fixture gains the surfaces these actions need — an editable field, a key-catch readout, a scrollable region, a hover surface, and a draggable element — so every action has an independent observable result that is read from the application's own state rather than from a screenshot guess.

The model-facing session, the desktop-wide overview, and cross-application task planning stay where they are. This change makes the actions available; a later change lets a model choose them.

## User Stories

1. As a user, I want to type text into an application, so that a model can fill in a field instead of only pressing a button.
2. As a user, I want typed text to include characters that are not on my keyboard layout, so that accented and non-Latin text can be entered reliably.
3. As a user, I want to send a key chord such as a modifier plus a letter, so that application shortcuts work.
4. As a user, I want the full set of ordinary navigation and editing keys, so that menus and text fields can be driven without guesswork.
5. As a user, I want an immediate, clear rejection of an unrecognized key name, so that a typo does not silently become a partial keypress.
6. As a user, I want to scroll a window vertically, so that content below the fold can be reached.
7. As a user, I want to scroll a window horizontally, so that wide content can be reached.
8. As a user, I want to specify where the scroll happens, so that one region scrolls rather than the whole window.
9. As a user, I want to move the pointer without pressing anything, so that hover-revealed menus and tooltips can be opened.
10. As a user, I want a hover to take a moment to travel when requested, so that applications that track movement respond the way they do for a person.
11. As a user, I want to drag an element from one point to another, so that items can be moved, reordered, or dropped.
12. As a user, I want a drag to look like a real drag to the application, so that controls that track movement mid-gesture behave correctly.
13. As a user, I want to right-click, so that context menus can be opened.
14. As a user, I want to double-click, so that items that open on a second click can be opened.
15. As a user, I want those click variants to keep the same safety checks as a single left-click, so that a new button cannot weaken the guard.
16. As a user, I want to bring a specific application to the front, so that a task can move between applications.
17. As a user, I want a minimized window restored as part of focusing it, so that switching does not fail on a minimized target.
18. As a user, I want a failed focus to be reported honestly, so that I know input was not delivered rather than assuming it was.
19. As a user, I want a focus change to discard the previous window's coordinates, so that a target chosen before switching is never used after it.
20. As a user, I want to require a fresh look at a window after switching to it, so that I never act on an image of a window that is no longer in front.
21. As a user, I want every action refused when the window moved or was resized since I looked at it, so that I do not click a different control than the one I chose.
22. As a user, I want every action refused when the target is covered by another window, so that a dialog in front does not receive my input.
23. As a user, I want every action refused when the screen is locked or the session has no interactive desktop, so that a failure is reported instead of guessed at.
24. As a user, I want every action bound to the image I actually saw, so that a decision is never made against a frame I never received.
25. As a user, I want the tool to report the physical point an action landed on, so that a surprising result can be traced back to a coordinate.
26. As a user, I want the tool to report dispatched, failed, and verified outcomes separately, so that a successful API call is never mistaken for a completed task.
27. As a user, I want a partially injected input sequence to be cleaned up and reported, so that a stuck mouse button or modifier does not silently remain held.
28. As a user, I want a long drag to be bounded, so that a bad request cannot spin indefinitely.
29. As a user, I want a very long string of typed text to be bounded or reported, so that a runaway request fails cleanly.
30. As a user, I want the same action vocabulary to work for a window with no useful accessibility data, so that a custom-rendered or game interface is controllable the same way.
31. As a user, I want these actions to keep working for the controlled fixture workflow, so that existing verified behavior is not regressed.
32. As a model author, I want a single uniform way to express a pointer target for every pointer action, so that I can translate an image region into an action without per-action special cases.
33. As a model author, I want a single stable name for each action and its arguments, so that my tool contract does not change when actions are added.
34. As a model author, I want to pass the observation I was shown along with my action, so that the tool can verify it is acting on the image I actually saw.
35. As a model author, I want key chords expressed as readable names rather than scan codes, so that my prompts and decisions stay portable.
36. As a model author, I want an unknown key name rejected before any input is injected, so that a malformed decision cannot half-execute.
37. As a model author, I want to know which window is in front, so that I can choose whether to switch or re-observe.
38. As a model author, I want a focus change to be reported as an explicit event, so that I re-observe before my next decision instead of trusting stale coordinates.
39. As an implementing agent, I want one shared guard used by every action, so that new actions cannot drift from the safety rules the click already satisfies.
40. As an implementing agent, I want the existing click behavior preserved exactly for a plain single left-click, so that the already verified click path and its tests keep passing unchanged.
41. As an implementing agent, I want fixture surfaces for the new actions added without disturbing the existing fixture window or its recognition profile, so that verified OCR and click results stay reproducible.
42. As an implementing agent, I want each new action verified against the application's own reported state, so that a dispatched event is not treated as a completed one.
43. As an implementing agent, I want a negative gate for every new action, so that each refusal path is proven to stop input rather than assumed.
44. As an implementing agent, I want the new action names discoverable from the engine's own capability report, so that a client can learn the vocabulary without hard-coding it.
45. As an implementing agent, I want the existing guard's checks to be individually reusable, so that a future action with different needs can reuse them individually.
46. As an implementing agent, I want limits recorded where they are real, so that a later milestone is not blocked by an undocumented assumption.

## Implementation Decisions

- The Rust engine remains the single owner of capture, window identity, observation identifiers, geometry and foreground checks, and input injection. The Python client remains a transport and orchestration layer that adds no desktop semantics of its own.
- The existing guarded click preamble is decomposed into individually reusable pre-dispatch checks rather than copied. Every new action is written as a composition of those shared checks plus its own injection sequence. The checks cover: window identity is still alive and matches the expected process and creation time; the interactive desktop is available; the attached window is the foreground window or its root owner; window bounds still match the bounds recorded with the observation; the caller's requested point lies inside the observed frame; the physical point resolves to the attached window by hit test; and the target observation is current and within the caller's age limit.
- Refactoring the click into the shared checks is part of this work and must be behavior-preserving. The already verified click outcome, its error codes, and its negative gates are the regression oracle.
- All pointer actions accept one uniform target argument expressed in observed-frame pixels. The caller may supply either a bounding box, whose centre is used, or a point. Both forms are validated against the frame extent, mapped to physical desktop coordinates through the bounds recorded with the observation, and hit-test owned before dispatch. A bare screen coordinate is not accepted, for any pointer action, for the same reason the click does not accept one.
- Pointer actions require an observation identifier and apply the existing age limit default. A point supplied by a caller that does not match the current observation is refused.
- Keyboard actions have no pointer target, so they bind to window identity and foreground instead of to a point. They accept an optional observation identifier; when one is supplied it is enforced exactly as for a pointer action. They never inject while another window is in front.
- Typing sends the requested string as Unicode key events, one UTF-16 code unit at a time, with a matching key-up for each key-down. Non-Basic-Multilingual-Plane characters are sent as their surrogate pairs, so no character is dropped or substituted. An optional per-character delay is exposed because a small number of applications drop characters from a single unthrottled batch. The request is length-bounded and reports the number of characters sent.
- A key chord is one call. Modifiers are written as a plus-separated prefix of named modifiers followed by a named key, for example a control-plus-shift-plus-letter combination. The recognized vocabulary covers the common modifiers, return, tab, escape, space, backspace, delete, insert, home, end, page keys, the four arrows, the function key range, and single printable characters. An unrecognized name is refused with a dedicated error code before any event is generated. A chord may be repeated a bounded number of times and may hold its keys for a requested duration.
- Scrolling is expressed in wheel notches at a uniform pointer target, horizontally and vertically, sent as per-notch events so that intermediate scroll messages reach the application. The notches per call are bounded and the bound is reported when it is hit. A scroll dispatched with no explicit target may be sent at the current pointer position, in which case the hit-test ownership check does not apply and the result says so.
- Hover moves the pointer to a target without changing any button state. An optional duration interpolates the movement so that applications tracking motion receive intermediate moves; without it the pointer is moved in a single jump, which is the more reliable default.
- Drag presses at a start target, sends a bounded number of interpolated intermediate moves, and releases at an end target. Both endpoints are expressed in the same observed-frame coordinate system and both are validated. Intermediate points are interpolated in physical screen space and are not re-hit-tested, because during a legitimate drag the pointer is expected to travel over content that is not the press target.
- Both drag endpoints must lie inside the observed frame. A drag that must leave the attached window, such as dropping a file onto another application, is not expressible until desktop-wide observation exists. This is recorded as a known limit rather than worked around.
- The click action gains an optional button selector of left, right, or middle, and an optional press count of one or two. Both default to the current single left-click, so existing callers and the existing verified path are unchanged. Every click variant runs the identical guard; only the injected flag and repetition differ.
- Explicit window focus becomes an operation of its own. It validates the target window's identity when a process identifier and creation time are supplied, restores the window if it is minimized, brings it to the top, requests foreground, and then verifies the result against the actual foreground window rather than trusting the request. It returns both the requested window and the window that was in front beforehand.
- Focus uses only documented, ordinary window-management calls. It does not use simulated modifier keystrokes or any other focus-stealing workaround. The existing resolution of the foreground restriction stands: a window that refuses foreground produces an honest refusal, not a forced takeover.
- A successful focus change increments the attached target's foreground epoch and clears the engine's stored observation, so coordinates and observations belonging to the previous foreground window are unusable afterwards. This is the mechanism that makes switching safe, independent of whether a later check would also catch it.
- Focusing a window does not by itself move the capture session. The engine continues to own exactly one capture target. The client composes a switch by focusing the chosen window, re-attaching the engine to it, and obtaining a fresh observation. A convenience entry point on the client performs that composition in one call so a caller cannot accidentally focus one window and keep observing another.
- A new, distinct error code reports a refused focus and names the window that actually holds the foreground, so a caller can report a recoverable situation rather than a generic failure. A second new code reports an unrecognized key name. All other refusals reuse the existing codes for stale observation, moved geometry, changed foreground, occluded target, invalid coordinates, inaccessible desktop, and failed injection, so existing callers do not need a new error taxonomy.
- Input injection goes through one shared helper that checks the count of events actually accepted, releases any pressed button on a partial failure, and reports the injected count. No action retries itself after a partial or ambiguous dispatch.
- The engine's capability report lists the action names it supports, so a client or a model-facing tool can discover the vocabulary rather than hard-coding it.
- The Python client gains one method per action, a shared helper that builds the uniform pointer target argument, and the switch convenience. Method names mirror the action names used on the wire. The client does not add freshness policy, re-observation, or verification; those belong to whoever sequences actions.
- The controlled fixture gains a second window, selected by a command-line argument when the executable starts, dedicated to the new actions. It provides an editable field, a readout that records recognised key chords, a scrollable region with a readout of its scroll offset, a hover surface with enter and leave readouts, a draggable element with a readout of where it was dropped, and surfaces that record right-clicks and double-clicks.
- The existing fixture window, its recognition profile, its labelled reference frames, and the recognition and click test suites are not modified. The new window is a separate window with its own title so the existing discovery and profile matching are unaffected.
- Each new fixture readout is exposed as a named element in the accessibility tree, so tests read the application's own state through the existing inspection operation rather than by reading pixels or running recognition. This keeps the new evidence independent of the recognition path it does not test.

## Testing Decisions

- The single seam is the existing one: the Python client driving the persistent Rust engine against a real native window on a real interactive desktop. Every new action is exercised through that seam, end to end, with the fixture application's own state as the oracle. No new lower-level seam is introduced for unit-testing the guard or the key table; those are implementation details behind a seam that already exists.
- A test asserts what the application did, not what the engine returned. A typed string is verified by reading the field's value. A chord is verified by the fixture's own key readout. A scroll is verified by a non-zero, and specifically changed, scroll offset. A hover is verified by the hover readout changing. A drag is verified by the recorded drop position, which also proves the gesture was continuous rather than a press and release at two unrelated points. A right-click and a double-click are verified by their own readouts.
- Every new action gets a refusal test proving no input was dispatched, checked by the same application-state oracle. The shared refusals are: a moved or resized window, a foreground change, an observation that is expired or does not match, a target point outside the frame, and a target covered by another window. Testing these repeatedly per action is deliberately limited to the actions where the guard is genuinely reachable with a different argument shape, plus one comprehensive set against the actions that share the most with the existing click.
- Typing, chords, and scrolling are tested for both a successful dispatch and a refusal after the window moves between observation and action, which is the most common real failure.
- Window switching is tested as a sequence: focus the second fixture window, confirm the engine reports the new foreground and the previous one, confirm that an observation from the first window is no longer usable, then attach and observe the new window and perform an action on it successfully.
- A refused focus is tested by targeting a window that cannot take the foreground, and the test asserts that the reported foreground is the one that actually holds it, rather than asserting on the engine's internal state.
- A partially completed gesture is not simulated in tests. The shared injection helper's release-on-partial-failure behaviour is covered by prior reasoning and by the existing click negative gates, and is recorded as untested rather than claimed as verified.
- The existing protocol, capture, inspection, recognition, guarded click, and workflow suites must all still pass unchanged after the guard refactor. The guarded click suite is the specific regression oracle for the refactor, and it is run before any new test is trusted.
- Prior art for every new test is the existing guarded-click suite: launch the fixture, attach to it, bring it to the front, observe, act, then read the application's state through inspection, and assert both the successful result and that refusals left the state untouched.
- Tests are ordered within the suite so that the application state each test depends on is either established by that test or read fresh, and so that a refusal test can assert the state is unchanged from the previous test.
- Recorded results state, per action, what was dispatched, what the application reported afterwards, and which refusals were proven. Latency is recorded as a measurement, not asserted as a threshold. Where a gate could not be run on this machine, the exact failure is recorded rather than the action being reported as verified.

## Out of Scope

- Letting the vision model choose among the new actions. The model-facing session keeps its single-click decision contract; only the action vocabulary underneath it grows.
- Desktop or multi-monitor overview capture, and any coordinate outside the currently attached window's observed frame.
- Expressing a drag whose endpoint lies outside the attached window.
- Held state across calls: a separately pressable button, a separately pressable modifier, or releasing a key pressed by a previous call.
- Clipboard access, text selection by dragging across content, multi-line paste semantics, or IME composition.
- Any change to the observation age default, to the freshness policy, or to the requirement to re-observe before acting.
- Semantic actions beyond those already supported by UI Automation, and any reconstruction of a user interface tree from pixels.
- Recognizing application state by text in these tests. The new evidence is read from the application, not from optical recognition.
- A second provider adapter, an external tool host, installation and startup packaging, unattended or elevated operation, and any change to the model-verification path.
- Any claim that all applications accept synthetic input, restore foreground on demand, or tolerate rapid unthrottled typing.
- Continued application-specific optimization for any particular game.

## Further Notes

This is the direct continuation of the previous specification, which deliberately excluded typing, key chords, scrolling, dragging, and application switching from its first slice so that the smallest image-to-action loop could be proven first. That loop is now demonstrated and recorded. The action vocabulary is the remaining gap between it and a general desktop agent.

The product roadmap treats reliable interaction as its second delivery milestone and lists typed text, chords, scrolling, movement, drag, right and double click, discovery, and explicit focus and switching as its contents. Right-click and double-click are included here as a small extension of the existing click rather than as separate actions, because they need no new guard and no new client surface; if scope must be cut, they are the first thing to drop.

The roadmap's standing contract is that every action stays tied to the observation and the window identity the decision was made against, that switching windows invalidates old coordinates, and that a dispatched action is never reported as a verified one. The shared-guard decision exists to make that contract structural rather than a convention each new action has to remember.

Two known limits are recorded rather than solved here. A window that refuses foreground cannot be forced, and the tool reports that honestly instead of working around the operating system's focus policy. And a completely static window may yield no fresh frame, which the existing session already surfaces as a distinct outcome.
