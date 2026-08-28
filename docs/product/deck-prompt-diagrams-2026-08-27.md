# Deck revision prompt — add the two validation diagrams (2026-08-27)

> Paste everything below the horizontal rule into the same Claude Design conversation
> that holds the deck. Two SVGs are included in full; embed them verbatim rather than
> redrawing them, so the slides carry the exact figures.

---

Please add two slides to the deck, using the SVG sources given below. Keep the deck's
existing visual style — these figures already use the same palette, so they should sit on
the slide with no frame, no border, and no background panel of their own.

For each figure: embed the SVG **inline and verbatim**. Do not redraw it, do not restyle
it, and do not re-type its text. Scale it to the slide's content width while preserving
aspect ratio, and give it generous margins. The slide title should sit above the figure in
the deck's normal title style; the figure's own internal heading is part of the artwork and
should not be duplicated as slide text.

## New slide — place immediately BEFORE the existing "The generation gate" slide

Slide title: **Validation is four surfaces, not one**

Below the figure, add a single muted caption line:

> Each surface guards a different moment, and each one is allowed to say no.

Embed this SVG:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 620" width="1280" height="620" font-family="Pretendard, 'Segoe UI', 'Noto Sans KR', sans-serif">
  <rect width="1280" height="620" fill="#1a1a19"/>

  <text x="64" y="70" fill="#ffffff" font-size="30" font-family="'Iowan Old Style', Palatino, Georgia, serif">The validation layer is four surfaces, not one</text>
  <text x="64" y="102" fill="#898781" font-size="16">Each one guards a different moment. The V1–V9 gate is the third of them — it guards what is built, not what is edited.</text>

  <!-- timeline spine -->
  <line x1="64" y1="164" x2="1216" y2="164" stroke="rgba(255,255,255,0.07)"/>
  <text x="64" y="146" fill="#6a6862" font-size="12" letter-spacing="1.4">WHEN</text>

  <text x="152" y="146" fill="#6a6862" font-size="12">before an edit</text>
  <text x="452" y="146" fill="#6a6862" font-size="12">after a write</text>
  <text x="742" y="146" fill="#6a6862" font-size="12">before publishing a build</text>
  <text x="1042" y="146" fill="#6a6862" font-size="12">after a run</text>

  <!-- 1 constraint catalogue -->
  <rect x="64" y="196" width="272" height="286" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="88" y="230" fill="#6a6862" font-size="12" letter-spacing="1.4">01</text>
  <text x="88" y="260" fill="#ffffff" font-size="19">Constraint catalogue</text>
  <text x="88" y="292" fill="#898781" font-size="13.5">Checks a proposed edit before</text>
  <text x="88" y="312" fill="#898781" font-size="13.5">Vissim is touched at all.</text>
  <text x="88" y="348" fill="#4c8dff" font-size="26">117</text>
  <text x="88" y="372" fill="#6a6862" font-size="12.5">named messages, in three languages</text>
  <text x="88" y="410" fill="#c3c2b7" font-size="13">Includes reference integrity:</text>
  <text x="88" y="430" fill="#c3c2b7" font-size="13">a link still referenced by</text>
  <text x="88" y="450" fill="#c3c2b7" font-size="13">connectors cannot be removed.</text>

  <!-- 2 read-back -->
  <rect x="364" y="196" width="272" height="286" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="388" y="230" fill="#6a6862" font-size="12" letter-spacing="1.4">02</text>
  <text x="388" y="260" fill="#ffffff" font-size="19">Read-back verification</text>
  <text x="388" y="292" fill="#898781" font-size="13.5">The value written is compared</text>
  <text x="388" y="312" fill="#898781" font-size="13.5">against the value Vissim reports.</text>
  <text x="388" y="356" fill="#c3c2b7" font-size="13">This is the check neither prior</text>
  <text x="388" y="376" fill="#c3c2b7" font-size="13">SUMO agent performs — and the</text>
  <text x="388" y="396" fill="#c3c2b7" font-size="13">one a silent simulator requires.</text>
  <text x="388" y="436" fill="#898781" font-size="13">Backed by a shadow model, an</text>
  <text x="388" y="456" fill="#898781" font-size="13">append-only trace, and rollback.</text>

  <!-- 3 the gate  (highlighted) -->
  <rect x="664" y="196" width="272" height="286" rx="12" fill="rgba(224,133,119,0.10)" stroke="rgba(224,133,119,0.35)"/>
  <text x="688" y="230" fill="#e49787" font-size="12" letter-spacing="1.4">03</text>
  <text x="688" y="260" fill="#ffffff" font-size="19">Generation gate</text>
  <text x="688" y="284" fill="#e49787" font-size="15" font-family="'JetBrains Mono', Consolas, monospace">V1 – V9</text>
  <text x="688" y="316" fill="#c3c2b7" font-size="13.5">Judges a network the system</text>
  <text x="688" y="336" fill="#c3c2b7" font-size="13.5">generated, before it is published.</text>
  <text x="688" y="380" fill="#e49787" font-size="26">78</text>
  <text x="688" y="404" fill="#6a6862" font-size="12.5">finding codes across nine checks</text>
  <text x="688" y="440" fill="#c3c2b7" font-size="13">Six checks can refuse;</text>
  <text x="688" y="460" fill="#c3c2b7" font-size="13">three can only annotate.</text>

  <!-- 4 reality check -->
  <rect x="964" y="196" width="252" height="286" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="988" y="230" fill="#6a6862" font-size="12" letter-spacing="1.4">04</text>
  <text x="988" y="260" fill="#ffffff" font-size="19">Reality check</text>
  <text x="988" y="292" fill="#898781" font-size="13.5">Model volumes against</text>
  <text x="988" y="312" fill="#898781" font-size="13.5">observed counts.</text>
  <text x="988" y="356" fill="#4c8dff" font-size="26">GEH 5</text>
  <text x="988" y="380" fill="#6a6862" font-size="12.5">threshold, per movement and node</text>
  <text x="988" y="416" fill="#c3c2b7" font-size="13">A check that cannot run</text>
  <text x="988" y="436" fill="#c3c2b7" font-size="13">reports NOT_CHECKABLE</text>
  <text x="988" y="456" fill="#c3c2b7" font-size="13">with its reason.</text>

  <!-- closing -->
  <line x1="64" y1="524" x2="1216" y2="524" stroke="rgba(255,255,255,0.07)"/>
  <text x="64" y="562" fill="#ffffff" font-size="19">Each surface answers a different question, and each one is allowed to say no.</text>
  <text x="64" y="590" fill="#898781" font-size="14">Is this edit legal · did it actually take effect · is this network sound · does the model match reality</text>
</svg>
```

## Replace the body of the existing "The generation gate" slide

Keep the slide and its title. Delete its bullet list and put this figure in its place.

Then, beneath the figure, keep only two short lines — the diagram carries everything else:

- The gate refused networks **this system generated itself** — isolated regions that traffic
  could neither enter nor leave. That result was reported, not hidden.
- The gate is offline by construction; V1 is the one check that needs a live Vissim, and its
  evidence is what separates a pass from an incomplete.

Embed this SVG:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" font-family="Pretendard, 'Segoe UI', 'Noto Sans KR', sans-serif">
  <rect width="1280" height="720" fill="#1a1a19"/>

  <text x="64" y="70" fill="#ffffff" font-size="30" font-family="'Iowan Old Style', Palatino, Georgia, serif">The generation gate — V1 to V9</text>
  <text x="64" y="102" fill="#898781" font-size="16">Nine checks in a fixed order, split into two tiers. Six can stop a network; three can only annotate it.</text>

  <!-- ================= REFUSE TIER ================= -->
  <rect x="64" y="134" width="700" height="446" rx="12" fill="rgba(224,133,119,0.10)" stroke="rgba(224,133,119,0.35)"/>
  <text x="88" y="168" fill="#e49787" font-size="15" letter-spacing="0.6">REFUSE TIER — a fault here blocks publication</text>

  <line x1="88" y1="184" x2="740" y2="184" stroke="rgba(224,133,119,0.35)"/>

  <text x="88" y="216" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V1</text>
  <text x="140" y="216" fill="#ffffff" font-size="17">Load oracle</text>
  <text x="140" y="238" fill="#c3c2b7" font-size="13">A real PTV Vissim opens the file — the only check that can prove it.</text>

  <text x="88" y="282" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V2</text>
  <text x="140" y="282" fill="#ffffff" font-size="17">Reference integrity</text>
  <text x="140" y="304" fill="#c3c2b7" font-size="13">Every id points at an object that exists, in the id space it belongs to.</text>

  <text x="88" y="348" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V3</text>
  <text x="140" y="348" fill="#ffffff" font-size="17">Connectivity</text>
  <text x="140" y="370" fill="#c3c2b7" font-size="13">Traffic can enter and leave every region — no isolated pockets.</text>

  <text x="88" y="414" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V4</text>
  <text x="140" y="414" fill="#ffffff" font-size="17">Lane continuity</text>
  <text x="140" y="436" fill="#c3c2b7" font-size="13">Lane counts stay consistent across every connection; no distance is invented.</text>

  <text x="88" y="480" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V5</text>
  <text x="140" y="480" fill="#ffffff" font-size="17">Signal and geometry consistency</text>
  <text x="140" y="502" fill="#c3c2b7" font-size="13">Every signal head, group and controller resolves to real geometry.</text>

  <text x="88" y="546" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V6</text>
  <text x="140" y="546" fill="#ffffff" font-size="17">Demand coverage</text>
  <text x="140" y="568" fill="#c3c2b7" font-size="13">Structure must cover the network it claims; capacity is a warning, not a refusal.</text>

  <!-- ================= WARN TIER ================= -->
  <rect x="796" y="134" width="420" height="262" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="820" y="168" fill="#898781" font-size="15" letter-spacing="0.6">WARN TIER — named, never silent, never blocking</text>
  <line x1="820" y1="184" x2="1192" y2="184" stroke="rgba(255,255,255,0.07)"/>

  <text x="820" y="220" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V7</text>
  <text x="872" y="220" fill="#ffffff" font-size="16">Geometry sanity</text>

  <text x="820" y="268" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V8</text>
  <text x="872" y="268" fill="#ffffff" font-size="16">Conflict coverage</text>

  <text x="820" y="316" fill="#ffffff" font-size="17" font-family="'JetBrains Mono', Consolas, monospace">V9</text>
  <text x="872" y="316" fill="#ffffff" font-size="16">Measurement readiness</text>

  <text x="820" y="362" fill="#898781" font-size="13">A warning is written down with its reason.</text>
  <text x="820" y="382" fill="#898781" font-size="13">Discarding something unnamed is what is forbidden.</text>

  <!-- ================= VERDICT ================= -->
  <rect x="796" y="420" width="420" height="160" rx="12" fill="none" stroke="rgba(255,255,255,0.10)"/>
  <text x="820" y="452" fill="#4c8dff" font-size="15" letter-spacing="0.6">ONE VERDICT, THREE VALUES</text>

  <text x="820" y="486" fill="#ffffff" font-size="13" font-family="'JetBrains Mono', Consolas, monospace">PASS</text>
  <text x="924" y="486" fill="#898781" font-size="13">every refuse-tier check genuinely passed</text>

  <text x="820" y="516" fill="#e49787" font-size="13" font-family="'JetBrains Mono', Consolas, monospace">REFUSED</text>
  <text x="924" y="516" fill="#898781" font-size="13">a refuse-tier check proved a fault</text>

  <text x="820" y="546" fill="#e49787" font-size="13" font-family="'JetBrains Mono', Consolas, monospace">INCOMPLETE</text>
  <text x="924" y="546" fill="#898781" font-size="13">no fault, but a check could not run</text>

  <!-- closing line -->
  <line x1="64" y1="616" x2="1216" y2="616" stroke="rgba(255,255,255,0.07)"/>
  <text x="64" y="652" fill="#ffffff" font-size="19">A check that could not run is never a pass.</text>
  <text x="64" y="680" fill="#898781" font-size="14">Without V1's live evidence the verdict is INCOMPLETE with V1 named — not a green light. 78 distinct finding codes across the nine checks.</text>
</svg>
```

## One consistency edit

On the agent-loop slide, if it lists the constraint catalogue and read-back verification,
leave it unchanged — the new "four surfaces" slide is the wider map, and the two slides are
meant to agree, not to merge.
