# Deck revision prompt — fill the two remaining diagram placeholders (2026-08-27)

> Paste everything below the horizontal rule into the same Claude Design conversation
> that holds the deck. Two SVGs are included in full; embed them verbatim rather than
> redrawing them — they use the deck's own palette and fonts, so they sit on the slide
> with no frame, no border, and no background panel of their own.

---

Please make three edits to the deck.

## 1. On the slide titled "The pipeline"

Replace the dashed placeholder box `[DIAGRAM — pipeline: …]` with this SVG, embedded
inline and verbatim. Scale to the slide's content width, preserve aspect ratio, generous
margins. Do not redraw or re-type any of its text. The slide keeps its existing title;
the figure carries no heading of its own.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 400" width="1280" height="400" font-family="Pretendard, 'Segoe UI', 'Noto Sans KR', sans-serif">
  <rect width="1280" height="400" fill="#1a1a19"/>

  <!-- 01 Scoping — not claimed -->
  <rect x="64" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(255,255,255,0.05)"/>
  <text x="88" y="106" fill="#4a4844" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">01</text>
  <text x="214" y="106" fill="#4a4844" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">NOT IN DEMO</text>
  <text x="88" y="140" fill="#6a6862" font-size="19">Scoping</text>
  <text x="88" y="170" fill="#4a4844" font-size="13.5">problem framing,</text>
  <text x="88" y="190" fill="#4a4844" font-size="13.5">study scope</text>

  <line x1="232" y1="172" x2="246" y2="172" stroke="#6a6862"/>
  <polygon points="246,168 253,172 246,176" fill="#6a6862"/>

  <!-- 02 Data — covered -->
  <rect x="254" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="278" y="106" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">02</text>
  <text x="404" y="106" fill="#4c8dff" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">SCENE 2</text>
  <text x="278" y="140" fill="#ffffff" font-size="19">Data</text>
  <text x="278" y="170" fill="#898781" font-size="13.5">counts &amp; signal</text>
  <text x="278" y="190" fill="#898781" font-size="13.5">sheets, via adapters</text>

  <line x1="422" y1="172" x2="436" y2="172" stroke="#6a6862"/>
  <polygon points="436,168 443,172 436,176" fill="#6a6862"/>

  <!-- 03 Build — covered -->
  <rect x="444" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="468" y="106" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">03</text>
  <text x="594" y="106" fill="#4c8dff" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">SCENE 1</text>
  <text x="468" y="140" fill="#ffffff" font-size="19">Build</text>
  <text x="468" y="170" fill="#898781" font-size="13.5">map &#8594; draft &#8594;</text>
  <text x="468" y="190" fill="#898781" font-size="13.5">gate &#8594; .inpx</text>

  <line x1="612" y1="172" x2="626" y2="172" stroke="#6a6862"/>
  <polygon points="626,168 633,172 626,176" fill="#6a6862"/>

  <!-- 04 Calibrate — surface shown, limit named -->
  <rect x="634" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(224,133,119,0.35)"/>
  <text x="658" y="106" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">04</text>
  <text x="784" y="106" fill="#e49787" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">SCENE 5</text>
  <text x="658" y="140" fill="#ffffff" font-size="19">Calibrate</text>
  <text x="658" y="170" fill="#898781" font-size="13.5">score vs observed,</text>
  <text x="658" y="190" fill="#898781" font-size="13.5">AI proposals</text>
  <text x="658" y="218" fill="#e49787" font-size="12.5">field validation pending</text>

  <line x1="802" y1="172" x2="816" y2="172" stroke="#6a6862"/>
  <polygon points="816,168 823,172 816,176" fill="#6a6862"/>

  <!-- 05 Analyze — covered -->
  <rect x="824" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="848" y="106" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">05</text>
  <text x="974" y="106" fill="#4c8dff" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">SCENES 3&#8211;4</text>
  <text x="848" y="140" fill="#ffffff" font-size="19">Analyze</text>
  <text x="848" y="170" fill="#898781" font-size="13.5">real Vissim runs,</text>
  <text x="848" y="190" fill="#898781" font-size="13.5">curated dashboard</text>

  <line x1="992" y1="172" x2="1006" y2="172" stroke="#6a6862"/>
  <polygon points="1006,168 1013,172 1006,176" fill="#6a6862"/>

  <!-- 06 Report — covered -->
  <rect x="1014" y="72" width="164" height="200" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="1038" y="106" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">06</text>
  <text x="1164" y="106" fill="#4c8dff" font-size="11" text-anchor="end" font-family="'JetBrains Mono', Consolas, monospace">SCENE 6</text>
  <text x="1038" y="140" fill="#ffffff" font-size="19">Report</text>
  <text x="1038" y="170" fill="#898781" font-size="13.5">firm-styled DOCX,</text>
  <text x="1038" y="190" fill="#898781" font-size="13.5">absences named</text>

  <!-- legend -->
  <text x="64" y="330" fill="#6a6862" font-size="12" font-family="'JetBrains Mono', Consolas, monospace"><tspan fill="#4c8dff">SCENE n</tspan> — shown live in this talk&#160;&#160;&#160;&#160;<tspan fill="#e49787">warm outline</tspan> — surface shown, its limit named on the slide&#160;&#160;&#160;&#160;<tspan fill="#4a4844">dimmed</tspan> — not claimed today</text>
</svg>
```

## 2. On the slide titled "The agent loop"

Replace the dashed placeholder box `[DIAGRAM — agent loop: …]` with this SVG, same
embedding rules. The slide keeps its numbered list on the right and its caption below.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 560" width="960" height="560" font-family="Pretendard, 'Segoe UI', 'Noto Sans KR', sans-serif">
  <rect width="960" height="560" fill="#1a1a19"/>

  <!-- refuse exit above constraint check -->
  <text x="788" y="30" fill="#e49787" font-size="12" text-anchor="middle" font-family="'JetBrains Mono', Consolas, monospace">REFUSE — named reason, nothing written</text>
  <line x1="788" y1="64" x2="788" y2="44" stroke="rgba(224,133,119,0.35)"/>
  <polygon points="784,44 788,37 792,44" fill="#e49787"/>

  <!-- row 1 -->
  <rect x="64" y="64" width="216" height="132" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="88" y="98" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">01</text>
  <text x="88" y="130" fill="#ffffff" font-size="19">Plan</text>
  <text x="88" y="160" fill="#898781" font-size="13.5">the edit, and the</text>
  <text x="88" y="180" fill="#898781" font-size="13.5">reason for it</text>

  <line x1="284" y1="130" x2="364" y2="130" stroke="#6a6862"/>
  <polygon points="364,126 371,130 364,134" fill="#6a6862"/>

  <rect x="372" y="64" width="216" height="132" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="396" y="98" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">02</text>
  <text x="396" y="130" fill="#ffffff" font-size="19">Tool call</text>
  <text x="396" y="160" fill="#898781" font-size="13.5">87 tools, typed schemas,</text>
  <text x="396" y="180" fill="#898781" font-size="13.5">strict dispatch</text>

  <line x1="592" y1="130" x2="672" y2="130" stroke="#6a6862"/>
  <polygon points="672,126 679,130 672,134" fill="#6a6862"/>

  <rect x="680" y="64" width="216" height="132" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="704" y="98" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">03</text>
  <text x="704" y="130" fill="#ffffff" font-size="19">Constraint check</text>
  <text x="704" y="160" fill="#898781" font-size="13.5">117 named constraints,</text>
  <text x="704" y="180" fill="#898781" font-size="13.5">before Vissim is touched</text>

  <!-- down the right side -->
  <line x1="788" y1="196" x2="788" y2="348" stroke="#6a6862"/>
  <polygon points="784,348 788,355 792,348" fill="#6a6862"/>

  <!-- row 2 (flow runs right to left) -->
  <rect x="680" y="356" width="216" height="132" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="704" y="390" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">04</text>
  <text x="704" y="422" fill="#ffffff" font-size="19">Human approval</text>
  <text x="704" y="452" fill="#898781" font-size="13.5">no write without a</text>
  <text x="704" y="472" fill="#898781" font-size="13.5">recorded approval</text>

  <line x1="672" y1="422" x2="596" y2="422" stroke="#6a6862"/>
  <polygon points="596,426 589,422 596,418" fill="#6a6862"/>

  <rect x="372" y="356" width="216" height="132" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="396" y="390" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">05</text>
  <text x="396" y="422" fill="#ffffff" font-size="19">Apply over COM</text>
  <text x="396" y="452" fill="#898781" font-size="13.5">the job queue owns</text>
  <text x="396" y="472" fill="#898781" font-size="13.5">Vissim, never a handler</text>

  <line x1="364" y1="422" x2="288" y2="422" stroke="#6a6862"/>
  <polygon points="288,426 281,422 288,418" fill="#6a6862"/>

  <rect x="64" y="356" width="216" height="132" rx="12" fill="none" stroke="rgba(76,141,255,0.35)"/>
  <text x="88" y="390" fill="#6a6862" font-size="12" letter-spacing="1.4" font-family="'JetBrains Mono', Consolas, monospace">06</text>
  <text x="88" y="422" fill="#ffffff" font-size="19">Read-back verify</text>
  <text x="88" y="452" fill="#898781" font-size="13.5">written value vs what</text>
  <text x="88" y="472" fill="#898781" font-size="13.5">Vissim itself reports</text>

  <!-- back up the left side: the loop -->
  <line x1="172" y1="356" x2="172" y2="204" stroke="#6a6862"/>
  <polygon points="168,204 172,197 176,204" fill="#6a6862"/>
  <text x="184" y="284" fill="#6a6862" font-size="11" font-family="'JetBrains Mono', Consolas, monospace">next edit</text>

  <!-- refuse exits below row 2 -->
  <line x1="788" y1="488" x2="788" y2="508" stroke="rgba(224,133,119,0.35)"/>
  <polygon points="784,508 788,515 792,508" fill="#e49787"/>
  <text x="788" y="536" fill="#e49787" font-size="12" text-anchor="middle" font-family="'JetBrains Mono', Consolas, monospace">REJECTED — nothing written</text>

  <line x1="172" y1="488" x2="172" y2="508" stroke="rgba(224,133,119,0.35)"/>
  <polygon points="168,508 172,515 176,508" fill="#e49787"/>
  <text x="172" y="536" fill="#e49787" font-size="12" text-anchor="middle" font-family="'JetBrains Mono', Consolas, monospace">MISMATCH — rollback, trace kept</text>
</svg>
```

## 3. On the closing "Thank you" slide

Replace the literal placeholder line `Presenter name · Lab name` with:

> Yunha Lee · TUPA

(If the deck tool asks, this is plain text in the slide's existing muted mono style —
no other change to that slide.)
