# Windows programmatic text input: SendInput, TSF, and correct alternatives

Context: `wcu-engine` types text with `SendInput` + `KEYEVENTF_UNICODE`
(one key-down/key-up pair per char, `wVk=0`, `wScan` = UTF-16 unit).
Byte-perfect against a plain WinForms textbox; mangled by Windows 11
Notepad (XAML + TSF), e.g. `"wcu typed this sentence."` arrives as
`"wcu ........"`. Timing/chunking/batching changes did not help.
Clipboard+paste works but has ownership drawbacks.

All claims below cite the primary source that owns them
(Microsoft Learn / Win32 API reference). Hypotheses are labeled as such.

---

## 1. `keybd_event` vs `SendInput`: status and functional differences

### 1.1 Deprecation status (exact wording)

- `keybd_event`: "**Note** This function has been superseded. Use
  `SendInput` instead."
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-keybd_event>
- `mouse_event`: identical note, "**Note** This function has been
  superseded. Use `SendInput` instead."
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mouse_event>
- `VkKeyScan` (A/W): "This function has been superseded by the
  `VkKeyScanEx` function."
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-vkkeyscana>

### 1.2 Return values: silent failure vs counted insertion

- `keybd_event` returns `VOID` — nothing. A blocked or dropped event is
  invisible to the caller.
  Source: `keybd_event` page (syntax block), same URL as above.
- `SendInput` returns `UINT`: "the number of events that it successfully
  inserted into the keyboard or mouse input stream. If the function
  returns zero, the input was already blocked by another thread. To get
  extended error information, call `GetLastError`."
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput>

### 1.3 Atomicity: the single load-bearing batching difference

- `SendInput` "inserts the events in the `INPUT` structures serially into
  the keyboard or mouse input stream. These events are **not interspersed
  with other keyboard or mouse input events** inserted either by the user
  (with the keyboard or mouse) or by calls to `keybd_event`,
  `mouse_event`, or other calls to `SendInput`."
  Source: `SendInput` page, same URL as above.
- The keyboard-input overview restates the contract: "To simulate an
  **uninterrupted series** of user input events, use the `SendInput`
  function," and "the return value indicates the number of input events
  successfully played."
  Source: <https://learn.microsoft.com/en-us/windows/win32/inputdev/about-keyboard-input>
  ("Simulating Input" section).
- Consequence for this engine: one `SendInput` call per char (or per
  down/up pair) is N separate injection points; the OS guarantees nothing
  about interleaving between them. One call carrying the whole string's
  `INPUT` array is a single uninterrupted insertion. Per-char calls also
  multiply the window in which a TSF text service, IME, or hook can
  interleave processing between units of what the app sees as one input.

### 1.4 `keybd_event` cannot send Unicode at all

- `keybd_event`'s `dwFlags` accepts exactly two values:
  `KEYEVENTF_EXTENDEDKEY` (0x0001) and `KEYEVENTF_KEYUP` (0x0002). There
  is no `KEYEVENTF_UNICODE` option.
  Source: `keybd_event` parameter table, same URL as above.
- `KEYEVENTF_UNICODE` (0x0004) exists only on `KEYBDINPUT.dwFlags`, i.e.
  only reachable via `SendInput`, and "can only be combined with the
  `KEYEVENTF_KEYUP` flag."
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput>
- Consequence: any `keybd_event`-based tool (e.g. PyAutoGUI-style typing)
  is structurally limited to virtual-key/shift-state simulation through
  the active keyboard layout. Characters with no layout mapping are
  unreachable for it; a `SendInput`-Unicode path and a `keybd_event` path
  are not equivalent transports, so their behavior diverges exactly on
  non-mappable characters and on apps that treat `VK_PACKET` input
  differently (§3).

### 1.5 UIPI / integrity-level behavior

- "`SendInput` … is subject to UIPI. Applications are permitted to inject
  input only into applications that are at an equal or lesser integrity
  level."
  Source: `SendInput` page remarks.
- UIPI failure is deliberately silent: "This function fails when it is
  blocked by UIPI. Note that **neither `GetLastError` nor the return
  value will indicate the failure was caused by UIPI blocking**."
  Source: `SendInput` page return-value section.
- Partial insertion is the observable signal: return value `< cInputs`
  means only a prefix was inserted (caller must check `uSent !=
  ARRAYSIZE(inputs)` exactly as the documented example does).
- Low-level hooks can observe injection and its integrity direction:
  `KBDLLHOOKSTRUCT.flags` bit 4 (`LLKHF_INJECTED`, 0x10) = "event was
  injected (from any process)"; bit 1 (`LLKHF_LOWER_IL_INJECTED`, 0x02) =
  injected from a lower-integrity process (bit 4 is also set then).
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-kbdllhookstruct>
- Related mechanism for message-based alternatives (§4): UIPI also
  filters cross-privilege window messages, adjustable per-window with
  `ChangeWindowMessageFilterEx` — but "**messages whose value is smaller
  than `WM_USER` are required to be passed through the filter,
  regardless of the filter setting**." `WM_SETTEXT` (0x000C),
  `EM_REPLACESEL` (0x00C2), `WM_PASTE` (0x0302 — see note below) straddle
  this line; `WM_SETTEXT`/`EM_*` below `WM_USER` (0x0400) cannot be
  UIPI-filtered.
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex>

### 1.6 Keyboard state is the caller's problem

- "This function does not reset the keyboard's current state. Any keys
  that are already pressed when the function is called might interfere
  with the events that this function generates. To avoid this problem,
  check the keyboard's state with the `GetAsyncKeyState` function and
  correct as necessary."
  Source: `SendInput` remarks (duplicated in the "Simulating Input"
  overview section).
- A held modifier (physical or stuck from an earlier partial injection)
  combines with subsequent synthetic `wVk` events but is irrelevant to
  `KEYEVENTF_UNICODE` payload (which carries no shift semantics, §2) —
  except that it can trigger accelerators/hotkeys in the target.

---

## 2. How `KEYEVENTF_UNICODE` input actually flows

### 2.1 What the kernel delivers: `VK_PACKET`

- "`KEYBDINPUT` … If `KEYEVENTF_UNICODE` is specified, `SendInput` sends
  a `WM_KEYDOWN` or `WM_KEYUP` message to the foreground thread's message
  queue with *wParam* equal to `VK_PACKET`."
  Source: `KEYBDINPUT` remarks, `ns-winuser-keybdinput` URL above.
- `VK_PACKET` = 0xE7: "Used to pass Unicode characters as if they were
  keystrokes. The `VK_PACKET` key is the low word of a 32-bit Virtual Key
  value used for non-keyboard input methods."
  Source: <https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes>
- Contract on the struct: "If the `dwFlags` member specifies
  `KEYEVENTF_UNICODE`, `wVk` must be 0" and "`wScan` specifies a Unicode
  character which is to be sent to the foreground application."
  Source: `KEYBDINPUT` members section.

### 2.2 `TranslateMessage` → `WM_CHAR` / `WM_UNICHAR`

- "`GetMessage` or `PeekMessage` obtains this message, passing the
  message to `TranslateMessage` posts a `WM_CHAR` message with the
  Unicode character originally specified by `wScan`. This Unicode
  character will automatically be converted to the appropriate ANSI value
  if it is posted to an ANSI window."
  Source: `KEYBDINPUT` remarks.
- General rule: `TranslateMessage` "translates virtual-key messages into
  character messages. The character messages are posted to the calling
  thread's message queue, to be read the next time the thread calls
  `GetMessage` or `PeekMessage`." `WM_KEYDOWN`+`WM_KEYUP` → `WM_CHAR` or
  `WM_DEADCHAR`.
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-translatemessage>
- `WM_CHAR` "contains the character code of the key" pressed; `wParam` is
  the UTF-16 code unit for Unicode-registered window classes, else the
  process code page.
  Source: <https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-char>
- `WM_UNICHAR` (0x0109) "is the same as `WM_CHAR`, except it uses UTF-32,
  whereas `WM_CHAR` uses UTF-16… designed to send or post Unicode
  characters to ANSI windows and can handle Unicode Supplementary Plane
  characters." Probe support with `wParam = UNICODE_NOCHAR`.
  Source: <https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-unichar>
- Caveat that does **not** apply to our path: `TranslateMessage`
  "produces `WM_CHAR` messages only for keys that are mapped to ASCII
  characters by the keyboard driver" — the `VK_PACKET` path bypasses the
  driver mapping because the character arrives verbatim in `wScan`
  (per `KEYBDINPUT` remarks). Do not read the ASCII-mapping sentence as
  limiting Unicode injection.

### 2.3 Layout and shift state do not matter for `wVk=0` + `wScan`

- By construction (`wVk` must be 0), no virtual key enters layout
  translation (`ToUnicode` translates "the specified virtual-key code
  and keyboard state"; there is none here).
  Source: `ToUnicode`:
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-tounicode>
- Contrast with what *is* layout-dependent, i.e. everything the hybrid
  `VkKeyScan` approach relies on:
  - `VkKeyScan[Ex]` translates "a character to the corresponding
    virtual-key code and shift state **for the current keyboard**"
    (superseded by `VkKeyScanEx`, which takes an explicit layout).
    Returns −1/−1 in both bytes "if the function finds no key."
    Numpad translations ignored; French-AltGr layout reports shift state
    6 (Ctrl+Alt internally).
    Source: `VkKeyScanA` page.
  - `MapVirtualKey MAPVK_VK_TO_CHAR` yields "an **unshifted** character
    value"; dead keys set the top bit; `'A'..'Z'` map to uppercase
    regardless of layout.
    Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mapvirtualkeya>
  - Physical identity lives in the scan code, not the virtual key:
    "The scan code … identifies the key pressed **regardless of the
    active keyboard layout**, as opposed to the character represented by
    the key", and `KEYEVENTF_SCANCODE` simulates "a physical keystroke
    regardless of which keyboard is currently being used."
    Sources: keyboard-input overview ("Scan Codes") and `KEYBDINPUT`
    remarks.
- Net: pure `KEYEVENTF_UNICODE` text is layout-proof; any
  `VkKeyScan`-derived `wVk`+shift prefix is layout-fragile (wrong layout
  ⇒ wrong char or −1 fallback ⇒ the exact substitution class of bug in
  the symptom). A hybrid sender must re-query the layout per target
  thread (`VkKeyScanEx`/`MapVirtualKeyEx`/`ToUnicodeEx` exist for this
  reason) or drop to the Unicode path for unmappable chars.

### 2.4 Surrogate pairs (non-BMP) are mandatory framing, not optional

- `SendInput` remarks: "the touch keyboard uses the surrogate macros
  defined in winnls.h to send input to the system" — i.e. first-party
  senders emit supplementary characters as surrogate pairs.
  Source: `SendInput` remarks.
- `WM_CHAR` "can send UTF-16 surrogate pairs … Starting with Windows
  Vista"; detect with `IS_HIGH_SURROGATE` / `IS_LOW_SURROGATE` /
  `IS_SURROGATE_PAIR`.
  Source: `WM_CHAR` remarks.
- Surrogate halves: high U+D800–U+DBFF, low U+DC00–U+DFFF; "Standalone
  surrogate code points … are invalid and are not supported. Their
  behavior is undefined."
  Source: <https://learn.microsoft.com/en-us/windows/win32/intl/surrogates-and-supplementary-characters>
- Rule: any code point > U+FFFF must be sent as **two** `KEYEVENTF_UNICODE`
  down/up pairs (high then low surrogate). A lone half is undefined
  behavior at the receiver. (BMP text — the Notepad repro — is unaffected
  by this; listed for completeness of the sender contract.)

### 2.5 Ordering / coalescing: what is and is not documented

- Documented coalescing exists only for **autorepeat**: "the system
  combines the messages into a single key down message and increments
  the repeat count" when `WM_KEYDOWN`s arrive faster than the app
  processes them; `WM_KEYUP` repeat count is always 1.
  Source: keyboard-input overview ("Repeat Count").
- `WM_CHAR.lParam` explicitly degrades: "There is **not necessarily a
  one-to-one correspondence** between keys pressed and character messages
  generated, and so the information in the high-order word of the
  *lParam* parameter is generally not useful."
  Source: `WM_CHAR` page (repeated on `WM_UNICHAR`).
- There is **no documented** merging, reordering, or dropping of distinct
  `VK_PACKET`/`WM_CHAR` units in the Win32 layer. The observed
  many-chars→many-dots corruption therefore has no explanation at the
  `SendInput`→`TranslateMessage`→`WM_CHAR` layer; the next layer that can
  legally rewrite the stream is TSF (§3). Timing/chunking/batching
  experiments failing is consistent with this: the corruption is
  downstream of delivery.

### 2.6 `dwExtraInfo` / detectability of synthetic input (documented)

- `KEYBDINPUT.dwExtraInfo`: "An additional value associated with the
  keystroke. Use `GetMessageExtraInfo` to obtain this information."
  Default is 0 unless the sender sets it.
  Sources: `KEYBDINPUT` members;
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessageextrainfo>
- Independently of `dwExtraInfo`, injected keystrokes are tagged by the
  system: `LLKHF_INJECTED` (bit 4) is set for events "from any process"
  (`KBDLLHOOKSTRUCT`). Any low-level keyboard hook — and any input stack
  component that consumes hook information — can distinguish synthetic
  from physical keystrokes. This is the documented mechanism by which
  synthetic input *can* be treated differently from physical input.
  Source: `KBDLLHOOKSTRUCT` page.

---

## 3. Where TSF sits in the pipeline (and why Notepad ≠ WinForms textbox)

### 3.1 Architecture: the manager mediates everything

- Three components: **Applications** (display/edit/store text, expose it
  via COM interfaces to TSF), **Text Services** ("can obtain text from,
  and write text to, an application… implemented as a COM in-proc server
  that registers itself with TSF… Multiple text services can be
  installed"), **TSF Manager** ("functions as a mediator between an
  application and one or more text services. A text service **never
  interacts directly** with an application. All communication passes
  through the TSF manager… implemented by the operating system and
  cannot be replaced").
  Source: <https://learn.microsoft.com/en-us/windows/win32/tsf/architecture>
- Framework purpose: "delivery of advanced text input and natural
  language technologies… provides multilingual support and delivers text
  services such as keyboard processors, handwriting recognition, and
  speech recognition."
  Source: <https://learn.microsoft.com/en-us/windows/win32/tsf/text-services-framework>

### 3.2 TSF-aware apps consume text through a text store, not `WM_CHAR`

- "An application provides access to text by implementing a COM server
  that supports certain interfaces" — concretely, a text store exposing
  `ITextStoreACP` (ACP = zero-based character positions in the text
  stream), created into a context via
  `ITfDocumentMgr::CreateContext(m_ClientID, 0, (ITextStoreACP*)this, …)`
  and `Push`ed.
  Sources: architecture page; <https://learn.microsoft.com/en-us/windows/win32/tsf/text-stores>
- Access is lock-mediated: "The text store controls access to the text
  stream by using document locks… the manager must first install an
  advise sink" (`ITextStoreACPSink`), which also receives "notifications
  when the text store is modified by something other than the manager,
  such as user input through the application."
  Source: text-stores page.
- A plain Win32 EDIT control, by contrast, documents direct `WM_CHAR`
  handling: "`WM_CHAR` writes a character to the single-line edit
  control… writes a character to the multiline edit control. Handles the
  accelerator keys for standard functions."
  Source: <https://learn.microsoft.com/en-us/windows/win32/controls/about-edit-controls>
  (also: "Edit Controls Text Operations" documents `WM_COPY`/`WM_CUT`/
  `WM_CLEAR`/`WM_PASTE` as direct message handling).
- Architectural consequence: in a TSF-aware edit (RichEdit with TSF,
  XAML TextBox, modern Notepad), keystrokes pass through the TSF layer —
  thread manager, document manager/context, installed text services —
  before/with content landing in the store. In a raw EDIT control, the
  `WM_CHAR` lands directly. Same `SendInput` bytes, different consumer.

### 3.3 What a text service can legally do to the stream: compositions

- "The contents of the text store can be modified with a temporary input
  state called a **composition**."
  Source: text-stores page ("How To Modify the Text Store").
- Composition lifecycle (documented in the search-visible TSF
  "Compositions" reference, consistent with `ITextStoreACP` method docs):
  `InsertTextAtSelection` inserts initial text → `StartComposition`
  opens the mutable range → `SetText` updates it on new input →
  `EndComposition` commits. `ITextStoreACP::SetText` "sets the text
  selection to the supplied character positions"; called outside a
  composition "the TSF manager creates a composition that lasts just
  long enough to wrap the call."
  Sources (method reference):
  <https://learn.microsoft.com/en-us/windows/win32/api/textstor/nn-textstor-itextstoreacp>;
  `ITextStoreACP::SetText` reference (via `textstor` API set).
- What this means for the symptom: any installed **keyboard processor /
 spelling / correction / IME text service** may open a composition over
  freshly arrived keystrokes and replace its contents (correction,
  autocorrect, candidate commitment) before commit. A run of committed
  placeholder dots with occasional substitutions/duplications is the
  *shape* of composition churn (provisional display → replace on commit),
  not the shape of Win32-layer loss (which has no documented merge/drop
  for distinct `VK_PACKET` units, §2.5).

### 3.4 Documented reasons synthetic input can be treated differently

Three mechanisms, all primary-sourced; none requires speculating about
Notepad internals:

1. **No physical-key context.** A `VK_PACKET` carries a character, not a
   key: `wVk` must be 0, the payload is `wScan`-as-character, and the
   scan code — the identifier that is layout-independent and physical —
   is absent. Any text-service logic keyed on scan codes, key
   down/up pairing of real keys, or `GetKeyState`/`GetAsyncKeyState` at
   arrival time sees nothing physical. (Sources: `KEYBDINPUT` members +
   remarks; overview "Scan Codes"; `ToUnicode` signature.)
2. **Injected-ness is observable.** `LLKHF_INJECTED` tags every synthetic
   event (§2.6). Hooks and input processors can — and security or
   anti-spoofing components do — branch on it.
3. **TSF mediation is the normal path for keystrokes in aware apps.**
   Keystrokes reach the store through the manager + installed services
   (§3.1–3.2), each of which "can obtain text from, and write text to,
   an application." A WinForms textbox consuming `WM_CHAR` directly
   skips all of this — which is exactly the observed
   works-here/fails-there split.

### 3.5 Status of the dots hypothesis (labeled inference, not MSDN fact)

- Microsoft documents **no** "VK_PACKET is spell-checked into dots"
  rule. The load-bearing, sourced facts are: (a) delivery through
  `WM_CHAR` is verbatim per `KEYBDINPUT` remarks; (b) no Win32-layer
  coalescing of distinct characters is documented; (c) TSF-aware editors
  interpose manager + text services + compositions between keystroke and
  committed text, and those services may rewrite ranges before commit.
- Therefore the mangling is best modeled as **post-delivery rewrite
  inside the TSF/text-service layer** (composition/correction path), not
  as `SendInput` loss — which is why per-char timing, chunking, and
  batching experiments could not move it. Confirming the exact service
  (language/spellcheck/IME profile active on the test machine) is a
  runtime experiment (toggle input language, disable proofing/
  autocorrect, hook-trace `WM_CHAR` vs committed text), not a docs
  question. Fix direction follows regardless: stop simulating keystrokes
  into TSF editors; set text through a committed-text API (§4).

---

## 4. Correct alternatives that bypass keystroke simulation

Ordered by robustness. Preconditions are part of the contract — check
them at runtime.

### 4.1 UIA `ValuePattern::SetValue` — best for a *known, supporting* control

- `IValueProvider::SetValue(LPCWSTR val)` "sets the value of control";
  `S_OK` on success.
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcore/nf-uiautomationcore-ivalueprovider-setvalue>
- Provider-side conventions: "A control should have its `IsEnabled`
  property set to `TRUE` and its `IsReadOnly` property set to `FALSE`
  before allowing a call to `SetValue`." Multi-line edit controls "must
  implement `IValueProvider` if their contents can be changed";
  `IValueProvider` exposes no formatting/substring access (use
  `ITextProvider` for that).
  Source: <https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementingvalue>
- **Documented tension — handle in code:** the `SetValue` reference
  remarks say the opposite for stock edits: "Single-line edit controls
  support programmatic access … by implementing `IValueProvider`.
  However, **multi-line edit controls do not implement `IValueProvider`;
  instead they provide access … by implementing `ITextProvider`**."
  Source: `SetValue` remarks (same page as above).
  Practical rule: never assume — `QueryInterface`/pattern-availability
  check per element (`IsPatternAvailable`), then branch. Modern
  Notepad's document is multi-line; expect `ValuePattern` absent and
  `TextPattern` present (or partial support), and verify with Inspect
  SDK tool on the target build.
- General UIA framing (why this is the sanctioned driving API): "UI
  Automation … provide[s] information about the UI to end users and …
  manipulate the UI by **means other than standard input**"; providers
  "respond to programmatic input."
  Source: <https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview>

### 4.2 UIA `TextPattern` / text ranges — no `SetText` exists

- There is **no** `TextPattern::SetText`. The documented provider
  surface is `ITextProvider` (read-only content exposure per the Value
  pattern page: "Single and multi-line edit controls must implement
  `ITextProvider` to expose their read-only content") plus range
  operations. Writing through UIA to a multi-line edit therefore means:
  `SetValue` if offered; otherwise selection + typing/clipboard, or drop
  to a message-level API (§4.3–4.4) using the element's `HWND`
  (`UIA NativeWindowHandle` property).
  Sources: Value-pattern conventions page; `SetValue` remarks (both above).

### 4.3 `WM_SETTEXT` / `EM_REPLACESEL` / `EM_SETTEXTEX` — best for a known `HWND`

- `WM_SETTEXT` (0x000C): "Sets the text of a window… For an edit
  control, the text is the contents of the edit control." Replaces **all**
  text.
  Source: <https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-settext>
- `EM_REPLACESEL`: "Replaces the selected text in an edit control or a
  rich edit control with the specified text… If there is no selection,
  the replacement text is inserted at the caret." `wParam` = undo flag.
  Rich Edit 1.0+. "To replace all of the text, use `WM_SETTEXT`."
  Source: <https://learn.microsoft.com/en-us/windows/win32/controls/em-replacesel>
- `EM_SETTEXTEX`: "Combines the functionality of `WM_SETTEXT` and
  `EM_REPLACESEL`, and adds the ability to set text using a code page
  and to use either rich text or plain text" (code page 1200 = Unicode;
  RTF-detected input goes through the RTF reader).
  Source: <https://learn.microsoft.com/en-us/windows/win32/controls/em-settextex>
- UIPI advantage (sourced): all three are `< WM_USER` (0x000C, 0x00C2,
  0x0461? — verify per-message; `WM_SETTEXT`/`EM_REPLACESEL` certainly
  are), and sub-`WM_USER` messages "are required to be passed through
  the filter, regardless of the filter setting" (§1.5). `SendMessage`
  text-setting is therefore not subject to the silent UIPI input-block
  that affects `SendInput`.
- Limits: needs the target `HWND` (UIA→`NativeWindowHandle` or focus
  tracking); replaces selection/whole content rather than "typing"
  (undo grouping, change notifications, and caret placement differ —
  acceptable for a driver, but record the semantic gap); XAML islands
  may not honor classic edit messages — verify per target.

### 4.4 Clipboard + `WM_PASTE` / `EM_PASTESPECIAL` — universal but lossy-owned

- "`WM_PASTE` … copy the current content of the clipboard to the edit
  control at the current caret position. Data is inserted only if the
  clipboard contains data in `CF_TEXT` format." For combo boxes the edit
  portion handles it; `CBS_DROPDOWNLIST` ignores it.
  Source: <https://learn.microsoft.com/en-us/windows/win32/dataxchg/wm-paste>
- RichEdit variant: "paste the contents of the clipboard into a rich
  edit control by using `WM_PASTE`" (first recognized format) or
  `EM_PASTESPECIAL` for a specific format; `EM_CANPASTE` probes support.
  Source: <https://learn.microsoft.com/en-us/windows/win32/controls/use-rich-edit-clipboard-operations>
- Plain EDIT `WM_CHAR` handling already wires Ctrl+V to paste
  ("Handles the accelerator keys for standard functions, such as CTRL+C
  for copying and CTRL+V for pasting").
  Source: about-edit-controls page.
- Costs (engineering, partially non-MSDN): takes/restores global
  clipboard ownership (races with user/apps, `OpenClipboard` failures
  under contention, format limitations — classic `WM_PASTE` is
  `CF_TEXT`-gated per the docs). Works through TSF editors because the
  app performs the insertion itself as committed text — which is exactly
  why the paste path succeeds where keystroke simulation fails.

### 4.5 TSF-level injection — powerful, wrong direction for a driver

- Writing text *as* a text service means implementing a COM in-proc
  text service, registering with TSF, obtaining document locks
  (`ITextStoreACP::RequestLock` → `OnLockGranted`, read vs read/write),
  and editing via `InsertTextAtSelection`/`SetText` inside compositions.
  Sources: text-stores page; `ITextStoreACP` reference;
  TSF "Document Locks" and "Compositions" references (same doc set).
- Rank last for this use case: it requires the target app to already be
  TSF-bound with a cooperative lock position, plus registration and
  language-bar/profile side effects. It is the API for input-method
  vendors, not for driving arbitrary editors. (An in-house test text
  service would be a *diagnostic* instrument — to prove composition
  churn — not a shipping path.)

### 4.6 Ranking

| Situation | First choice | Fallback |
|---|---|---|
| Known control exposing `ValuePattern` (single-line, enabled, writable) | `SetValue` | `WM_SETTEXT` via `HWND` |
| Known multi-line / document (Notepad-class) | `EM_REPLACESEL` at caret (or `EM_SETTEXTEX`) via `HWND`; UIA `TextPattern` to locate caret/selection | Clipboard + `WM_PASTE` |
| Arbitrary focused editor, class unknown | UIA probe: `ValuePattern` → `TextPattern`+`HWND` messages → clipboard+paste | Keystroke simulation **last** |
| Keystroke simulation unavoidable | Single `SendInput` batch, pure `KEYEVENTF_UNICODE`, surrogate-correct, modifiers verified released | Per-char calls never |

---

## 5. Practical injection hygiene (all Microsoft-sourced)

### 5.1 Foreground / focus is a precondition, not a courtesy

- "The system posts keyboard messages to the message queue of the
  **foreground thread that created the window with the keyboard
  focus**." Focus shifts window-to-window; `GetFocus`/`SetFocus`,
  `WM_KILLFOCUS`/`WM_SETFOCUS` notifications.
  Source: keyboard-input overview ("Keyboard Focus and Activation").
- `KEYEVENTF_UNICODE` text goes "to the **foreground application**"
  (per `KEYBDINPUT` remarks) — there is no `HWND` parameter anywhere in
  the `SendInput` path. Focus-then-type races are structural.
- `SetForegroundWindow` is restricted: allowed only for desktop (non-UWP)
  callers when no menus are active and lock timeout expired / caller is
  foreground / started by foreground / received last input / debugging,
  etc.; otherwise "Windows flashes the taskbar button … to notify the
  user" instead of switching.
  Source: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow>
- Engine implication: verify focus *after* the switch (the existing
  120 ms settle + verify pattern) and re-verify immediately before the
  single batched `SendInput`; treat any failed focus assertion as a
  refusal, not a retry loop.

### 5.2 UIPI failure signature

- Return `< cInputs` (possibly 0) with `GetLastError` **not**
  discriminating UIPI from other blocks (§1.5). The engine's partial-failure
  path (release held modifiers, no retry) matches the API contract; add:
  log `uSent/cInputs` and integrity levels of both processes when a
  short insert occurs — that tuple is the only available UIPI evidence.

### 5.3 Batching: one call for the whole string

- One `SendInput(cInputs=N)` = serial, uninterrupted insertion (§1.3);
  N calls = N interleaving windows. The whole-string batch must still be
  surrogate-correct (§2.4): one BMP char = down+up; one non-BMP char =
  down+up(high) + down+up(low), i.e. 4 `INPUT`s.
- Check the return against N; on short insert, the tail was never
  delivered — resending the *whole* string duplicates the head; resend
  only the unsent suffix, or better, abandon keystroke simulation for a
  committed-text API (§4).

### 5.4 Layout pitfalls of `VkKeyScan`/`MapVirtualKey` hybrids

- `VkKeyScan` returns −1/−1 when "no key … translates to the passed
  character code" — every such char needs the Unicode path, not a best
  effort wrong key.
- `VkKeyScan` ignores the numpad, is main-section-only, and bakes in
  "the current keyboard" — the layout of the *calling thread*, not the
  target's. `VkKeyScanEx`/`MapVirtualKeyEx`/`ToUnicodeEx` take an `HKL`;
  without them the hybrid is wrong-layout-fragile by documentation.
- `MapVirtualKey MAPVK_VK_TO_CHAR` gives the *unshifted* character and
  flags dead keys via the top bit; `ToUnicode` consumes and mutates the
  kernel keyboard buffer ("changes the state of the kernel-mode keyboard
  buffer… might also cause undesired side-effects if used in conjunction
  with `TranslateMessage`"). Probing layouts with `ToUnicode` on the
  live thread perturbs real dead-key/Alt+numpad state — probe sparingly
  or on a scratch understanding of this side effect.
- `KEYEVENTF_SCANCODE` is the documented escape hatch when physical
  identity is wanted ("simulating a physical keystroke regardless of
  which keyboard is currently being used"), orthogonal to text payload.

### 5.5 Why `keybd_event`-based tools behave differently (sourced summary)

1. No `KEYEVENTF_UNICODE` exists on `keybd_event` (§1.4) — unmappable
   characters cannot be sent; behavior diverges exactly there.
2. `VOID` return — partial/UIPI blocks invisible (§1.2, §1.5).
3. One call per event — no serial-insertion guarantee across the
   sequence (§1.3); interleaving with user input, hooks, and text
   services is permitted by the contract.
4. Virtual-key traffic goes through layout translation and live keyboard
   state (`ToUnicode` semantics, shift/caps/dead keys), so identical
   scripts produce different text under different layouts — while the
   Unicode path is layout-invariant (§2.3).

---

## 6. Recommended fix direction for `windows-computer-use`

1. **Stop extending the keystroke-simulation path for editors.** The
   corruption has no documented Win32-delivery explanation and a fully
   documented TSF rewrite layer downstream; further timing/chunking
   experiments are spent budget.
2. **Type-into-editor := committed-text insertion.** Resolution order per
   target: UIA `ValuePattern::SetValue` (if offered, enabled, writable)
   → `EM_REPLACESEL`/`EM_SETTEXTEX` via resolved `HWND` →
   clipboard + `WM_PASTE`/`EM_PASTESPECIAL` (save/restore clipboard) →
   single-batch `SendInput` Unicode as last resort (games, canvases,
   IME-sensitive fields that only accept keystrokes).
3. **Keep `SendInput` for what only it can do**: chords, navigation keys,
   games, and non-text controls — with whole-string single-call batches,
   surrogate-correct encoding, pre-verified focus, modifier-state check
   (`GetAsyncKeyState`), and `uSent == cInputs` assertion with
   suffix-aware recovery.
4. **Verify Notepad's UIA surface on the test build** (Inspect SDK tool:
   `ValuePattern` vs `TextPattern` availability on the document element,
   `IsReadOnly`, `NativeWindowHandle`) before choosing between
   `SetValue`, `EM_REPLACESEL`, and paste. The docs disagree on stock
   multi-line `ValuePattern` support (§4.1) — runtime probing decides.
5. **Optional diagnostic (not shipping path):** compare `WM_CHAR`
   arrival (spy/hook) against committed text while toggling input
   language / proofing options; enables/disables the composition-churn
   model in §3.5 and names the responsible text service.

## 7. Runtime probing outcome on the test build (2026-09-30)

§6.4 required probing before choosing. Results, all observed live:

- Modern Notepad's document (`RichEditD2DPT`, `ControlType.Document`) exposes
  **both** `ValuePattern` (writable, `IsReadOnly=False`) and `TextPattern`,
  with a nonzero `NativeWindowHandle` distinct from the top-level `HWND`.
  The §4.1 docs tension resolves in favor of support: `SetValue` is viable,
  and the child `HWND` accepts messages.
- `ValuePattern::SetValue` via the existing `act set-value` path is
  byte-perfect, including `café 日本語 ✓` and non-BMP `𝄞`.
- `EM_REPLACESEL` (`0x00C2`, undoable) via `SendMessage` to the document's
  child `HWND` is byte-perfect with caret-insertion semantics — the closest
  match to typing, without replacing the whole document.
- Shipped as `act type --method commit`: one bounded `SendMessageTimeoutW`
  (`SMTO_ABORTIFHUNG`, 2 s) plus the same TextPattern selection-aware
  readback verification as paste. No clipboard, no input events.
- The WinForms fixture textbox exposes `TextPattern` to the COM engine (it
  appeared pattern-less only to the managed `.NET` UIA client), so commit
  verifies fully there too. A `ValuePattern`-only fallback covers editors
  without `TextPattern`, restricted to empty-document insertion where the
  expectation is computable without a selection.
- Probe side finding: `act set-value` / `invoke` / `focus` crashed with
  `AttributeError` (missing `--dry-run` plumbing); fixed by applying
  `_add_act_common` to those parsers.

## Source index (every citation above)

- `keybd_event`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-keybd_event>
- `SendInput`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput>
- `KEYBDINPUT`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput>
- `mouse_event`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mouse_event>
- Keyboard input overview (focus, keystroke flags, repeat count, scan codes, simulating input, layouts): <https://learn.microsoft.com/en-us/windows/win32/inputdev/about-keyboard-input>
- `TranslateMessage`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-translatemessage>
- `WM_CHAR`: <https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-char>
- `WM_UNICHAR`: <https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-unichar>
- `WM_KEYDOWN`: <https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-keydown>
- Virtual-key codes (`VK_PACKET` 0xE7): <https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes>
- Surrogates: <https://learn.microsoft.com/en-us/windows/win32/intl/surrogates-and-supplementary-characters>
- `VkKeyScan`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-vkkeyscana>
- `MapVirtualKey`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-mapvirtualkeya>
- `ToUnicode`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-tounicode>
- `GetMessageExtraInfo`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessageextrainfo>
- `KBDLLHOOKSTRUCT` (`LLKHF_INJECTED`): <https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-kbdllhookstruct>
- `SetForegroundWindow`: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow>
- `ChangeWindowMessageFilterEx` (UIPI): <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex>
- TSF framework: <https://learn.microsoft.com/en-us/windows/win32/tsf/text-services-framework>
- TSF architecture: <https://learn.microsoft.com/en-us/windows/win32/tsf/architecture>
- TSF text stores: <https://learn.microsoft.com/en-us/windows/win32/tsf/text-stores>
- `ITextStoreACP`: <https://learn.microsoft.com/en-us/windows/win32/api/textstor/nn-textstor-itextstoreacp>
- UIA overview: <https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview>
- UIA Value pattern: <https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementingvalue>
- `IValueProvider::SetValue`: <https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcore/nf-uiautomationcore-ivalueprovider-setvalue>
- `WM_SETTEXT`: <https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-settext>
- `EM_REPLACESEL`: <https://learn.microsoft.com/en-us/windows/win32/controls/em-replacesel>
- `EM_SETTEXTEX`: <https://learn.microsoft.com/en-us/windows/win32/controls/em-settextex>
- `WM_PASTE`: <https://learn.microsoft.com/en-us/windows/win32/dataxchg/wm-paste>
- RichEdit clipboard ops: <https://learn.microsoft.com/en-us/windows/win32/controls/use-rich-edit-clipboard-operations>
- Edit controls (about + text operations): <https://learn.microsoft.com/en-us/windows/win32/controls/about-edit-controls>
