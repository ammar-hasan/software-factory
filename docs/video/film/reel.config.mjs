// Software Factory — product film.
//
// Expanded from approved proof branch `proof-a`
// (proof identity afb0197c7624b767815097c9e1c50a0b5dc952e093325d1792b68c8ba02f2c20).
// See creative-brief.md for the direction and the rejection criteria, and
// claims.md for the source of every factual line in `vo`.
//
// Two rules hold the whole film together:
//   1. The progress marker is the subject's own stage machine, printed
//      verbatim from `sf stages`. The film advances through the factory.
//   2. If a machine said it, it is monospace inside a bordered plate. If a
//      person wrote it, it is not. No plate below is a paraphrase: every one
//      is transcribed from a command actually run against the repository, or
//      from a generated report in it.

const STAGES = [
  "INTAKE",
  "TRIAGE",
  "DESIGN",
  "BUILD",
  "REVIEW",
  "VERIFY",
  "HANDOFF",
  "COMPLETE",
];

// `.sg`, not `.stage`: the renderer gives its own background element
// class="stage" for backwards compatibility, and a `.stage` rule in theme.css
// would paint a rail bullet across the whole frame.
function rail(active) {
  const i = STAGES.indexOf(active);
  const rows = STAGES.map((s, n) => {
    const cls = n === i ? "sg on" : n < i ? "sg done" : "sg";
    return `<div class="${cls}">${s}</div>`;
  }).join("");
  return `<div class="rail"><div class="lab">STAGE</div>${rows}</div>`;
}

// A machine-output plate. Lines are joined with real newlines because .term is
// white-space: pre — never indent these to match the surrounding HTML.
function plate(lines, opts = {}) {
  const cls = ["term", opts.size || "", opts.cue == null ? "" : "cue"]
    .filter(Boolean)
    .join(" ");
  const attrs = [
    `class="${cls}"`,
    opts.cue == null ? "" : `data-cue="${opts.cue}"`,
    opts.delay == null ? "" : `data-delay="${opts.delay}"`,
  ]
    .filter(Boolean)
    .join(" ");
  return `<div ${attrs}>${lines.join("\n")}</div>`;
}

const chip = (text, cue, delay, hot) =>
  `<span class="tik${hot ? " hot" : ""} cue" data-cue="${cue}" data-delay="${delay}">${text}</span>`;

export default {
  title: "Software Factory",
  size: "16:9",
  assets: "assets",
  chrome: false,
  captions: { preset: "subtitle", size: 21 },
  // Two voices, cast for a reason: the narrator reports, and the project's
  // central claim is spoken by a second voice, because it is a quotation on
  // the record rather than the film's own assertion.
  voices: {
    a: { backend: "piper", speaker: "en_US-ryan-high", color: "#7c8cff", label: "narrator" },
    b: { backend: "piper", speaker: "en_GB-alan-medium", color: "#58e6d0", label: "the claim" },
  },
  theme: {
    mode: "dark",
    bg: "#0b0c10",
    stage: "#101218",
    panel: "#14161e",
    line: "#1e212b",
    ink: "#eceef5",
    muted: "#9096a8",
    faint: "#61667a",
    accent: "#7c8cff",
    "accent-dim": "#5566e8",
    green: "#4ec9a0",
    amber: "#e8b458",
    red: "#f2748c",
    css: "theme.css",
  },
  timing: { gapSentence: 0.24, gapTurn: 0.46, lead: 0.2, tail: 0.8 },
  provenance: {
    script: {
      authorship: "agent",
      note: "drafted from the repository's own README, PRD, generated trial report, and live command output",
    },
  },
  // Creator-owned assertions. The probes encode only what the metrics genuinely
  // represent; interpretive intent stays as prose rather than becoming an
  // invented number.
  assertions: [
    {
      id: "restrained-claim",
      class: "creative-hypothesis",
      expect:
        "The claim reads as a quotation under test rather than as the film's own boast: the frame holds still, entrances are the only motion, and the attribution is legible while the claim is on screen.",
      origin: { kind: "creative-brief", ref: "rejection criteria" },
      scope: { scene: "bet" },
      observe: [
        { metric: "video.static_ratio", operator: "gte", value: 0.6 },
        { metric: "video.cut_count", operator: "lte", value: 1 },
      ],
      riskyBecause: [
        "a dense still frame can read as inert",
        "a large confident setting can read as endorsement rather than quotation",
      ],
      questions: [
        "Does the attribution stay legible for as long as the claim is on screen?",
      ],
      related: {
        scene: "bet",
        source: "creative-brief.md",
        protected: ["quotation framing"],
      },
    },
    {
      id: "evidence-is-legible",
      class: "accessibility",
      expect:
        "Every machine-output plate is readable at 1280x720 and is never obscured by the caption band. Captions publish as SRT and VTT sidecars for the whole film.",
      origin: { kind: "production-requirement", ref: "brief: hard constraints" },
      observe: [{ metric: "caption.word_count", operator: "gte", value: 150 }],
      questions: [
        "Does any plate crowd the caption reserve at the bottom of the frame?",
      ],
      related: { source: "theme.css", protected: ["caption reserve"] },
    },
    {
      id: "no-ambient-motion",
      class: "deliberate-choice",
      expect:
        "Nothing moves except entrances. A film about a system that refuses to overclaim should not be restless.",
      origin: { kind: "creative-brief", ref: "animation: entrances only" },
      observe: [{ metric: "video.static_ratio", operator: "gte", value: 0.85 }],
      related: { source: "creative-brief.md", protected: ["camera rhythm"] },
    },
  ],
  scenes: [
    // ---------------------------------------------------------------- INTAKE
    {
      id: "intake",
      vo: [
        { who: "a", text: "Bugs. Feature requests. Escalations. Alerts." },
        {
          who: "a",
          text: "They arrive faster than anyone can work them. What is left over is the backlog.",
        },
      ],
      body: `<div class="f">
        ${rail("INTAKE")}
        <div class="col">
          <div class="eyebrow reveal">Intake</div>
          <p class="stmt reveal">Requests arrive faster than they can be worked.</p>
          <div class="queue">
            ${chip("The CSV importer mangles BOM headers", 0, "0.05")}
            ${chip("Reject duplicate keys", 0, "0.4")}
            ${chip("audit auth", 0, "0.75")}
            ${chip("make the parser handle BOMs", 1, "0.15")}
            ${chip("Line and column in errors", 1, "0.5")}
            ${chip("Make the errors better", 1, "0.85")}
            ${chip("audit export", 1, "1.2")}
            ${chip("a planted trailing-comma bug", 1, "1.55", true)}
          </div>
        </div>
      </div>`,
    },

    // ---------------------------------------------------------------- TRIAGE
    {
      id: "triage",
      vo: [
        {
          who: "a",
          text: "A software factory puts a coordinated fleet of specialist agents on that queue.",
        },
        {
          who: "a",
          text: "Intake, triage, design, build, review — one request carried through to a change a person can read.",
        },
      ],
      body: `<div class="f">
        ${rail("TRIAGE")}
        <div class="col">
          <div class="eyebrow reveal">The idea</div>
          <p class="stmt reveal">Specialists carry one request all the way to a reviewable change.</p>
          <div class="plate-cap cue" data-cue="1">planned, and deliberately not executed</div>
          ${plate(
            [
              `<span class="cmd">sf work "The CSV importer mangles BOM headers" \\</span>`,
              `<span class="cmd2">    --factory myfactory --repo ~/code/payments --dry-run</span>`,
              `The CSV importer mangles BOM headers (feature)`,
              `  planned stages: <span class="ok">TRIAGE → DESIGN → BUILD → REVIEW → HANDOFF</span>`,
              ``,
              `<span class="mut">dry run: nothing was executed</span>`,
            ],
            { cue: 1, delay: 0.18, size: "sm" },
          )}
        </div>
      </div>`,
    },

    // ---------------------------------------------------------------- DESIGN
    {
      id: "bet",
      vo: [
        { who: "a", text: "Underneath all of it sits a single claim." },
        {
          who: "b",
          text: "A modest model inside an excellent harness beats a frontier model inside a poor one.",
        },
        {
          who: "a",
          text: "That is a hypothesis. The experiment written to falsify it is pre-registered.",
        },
      ],
      body: `<div class="f">
        ${rail("DESIGN")}
        <div class="col">
          <div class="eyebrow reveal">The bet · stated as a claim under test</div>
          <div class="quote cue" data-cue="1">
            <p class="stmt">A modest model inside an <span class="accentword">excellent harness</span> beats a frontier model inside a poor one.</p>
          </div>
          <div class="hair cue" data-cue="2" data-grow></div>
          <div class="attrib cue" data-cue="2" data-delay="0.15">the project's own hypothesis &mdash; <b>not a result</b>. The experiment written to falsify it is pre-registered.</div>
        </div>
      </div>`,
    },

    // ----------------------------------------------------------------- BUILD
    {
      id: "mechanisms",
      vo: [
        { who: "a", text: "Every subsystem exists because that claim depends on it." },
        {
          who: "a",
          text: "A context pack, budgeted and cited. Typed tools, so anything computable is computed. Confidence scored against outcomes. A blast radius the machine checks.",
        },
      ],
      body: `<div class="f">
        ${rail("BUILD")}
        <div class="col">
          <div class="eyebrow reveal">What the agent gets</div>
          <p class="stmt sm reveal">The model is not the product. The harness is.</p>
          <ul class="mechs">
            <li class="mech cue" data-cue="1" data-delay="0"><b>Awareness</b><span>a budgeted, cited, deterministically assembled context pack</span></li>
            <li class="mech cue" data-cue="1" data-delay="1.1"><b>Tools</b><span>a typed registry — anything computable is computed</span></li>
            <li class="mech cue" data-cue="1" data-delay="2.2"><b>Confidence</b><span>calibration scored against outcomes</span></li>
            <li class="mech cue" data-cue="1" data-delay="3.2"><b>Courage</b><span>a machine-checked blast-radius contract</span></li>
          </ul>
        </div>
      </div>`,
    },

    // ---------------------------------------------------------------- REVIEW
    {
      id: "gate",
      vo: [
        {
          who: "a",
          text: "Gates block. The keystone is regression proven: fix a defect and you owe a test that failed at the parent commit, for the right reason.",
        },
        {
          who: "a",
          text: "A test that fails on a missing import proves the code did not exist. It is refused.",
        },
      ],
      body: `<div class="f">
        ${rail("REVIEW")}
        <div class="col">
          <div class="eyebrow reveal">The keystone gate</div>
          <p class="stmt sm reveal">It reads the failure's <span class="accentword">class</span>, not its existence.</p>
          <div class="plate-cap reveal">docs/trials.md · generated by scripts/run_trials.py</div>
          ${plate(
            [
              `BUILD  tests-pass         <span class="ok">pass</span> — 3 tests`,
              `BUILD  regression-proven  <span class="ok">pass</span> — 1 test(s) fail at parent on an assertion and pass at tip`,
            ],
            { cue: 0, delay: 1.1, size: "sm" },
          )}
          ${plate(
            [
              `BUILD  regression-proven  <span class="no">FAIL</span> — import failure`,
              `<span class="mut">       the test failed before its body ran, so it proves the code did not</span>`,
              `<span class="mut">       exist, not that the behaviour was wrong</span>`,
            ],
            { cue: 1, delay: 0.2, size: "sm" },
          )}
        </div>
      </div>`,
    },

    // ---------------------------------------------------------------- VERIFY
    {
      id: "honesty",
      vo: [
        { who: "a", text: "A metric with no data reports its absence, not zero." },
        {
          who: "a",
          text: "Ask what the central bet has been shown, and today the answer is insufficient data. No trials recorded.",
        },
        { who: "a", text: "A test keeps it that way." },
      ],
      body: `<div class="f">
        ${rail("VERIFY")}
        <div class="col">
          <div class="eyebrow reveal">Honesty is a feature</div>
          <p class="stmt sm reveal">Unavailable, with a reason. Never zero.</p>
          <div class="two">
            <img class="card cue" data-cue="0" data-delay="0.6" src="assets/dashboard-autonomy.png" alt="A dashboard card reading: Autonomy, merged, no human commits — not observable. No git-host adapter is configured, so this cannot be observed; reporting zero here would read as a factory that produces none.">
            <div class="side">
              ${plate(
                [
                  `<span class="cmd">sf experiment status</span>`,
                  `<span class="hold">insufficient_data</span> — no trials recorded`,
                ],
                { cue: 1, delay: 1.1, size: "sm" },
              )}
              <div class="attrib cue" data-cue="2" data-delay="0.1">The central experiment has its full protocol and <b>no trials</b>. That is the state of the claim today, reported rather than rounded.</div>
            </div>
          </div>
        </div>
      </div>`,
    },

    // --------------------------------------------------------------- HANDOFF
    {
      id: "local",
      vo: [
        {
          who: "a",
          text: "Same definition files, same harness, same guarantees — on a laptop, or in the cloud.",
        },
        { who: "a", text: "No control plane you don't own." },
      ],
      body: `<div class="f">
        ${rail("HANDOFF")}
        <div class="col">
          <div class="eyebrow reveal">Local-first</div>
          <p class="stmt sm reveal">No control plane you don't own.</p>
          ${plate(
            [
              `<span class="cmd">sf providers</span>`,
              ` tier         provider  model        endpoint                                  state`,
              ` local-small  local     local-model  http://127.0.0.1:11434/v1 (this machine)  <span class="ok">ready</span>`,
              ` mid          local     local-model  http://127.0.0.1:11434/v1 (this machine)  <span class="ok">ready</span>`,
            ],
            { cue: 0, delay: 1.0, size: "sm" },
          )}
          ${plate(
            [
              `<span class="cmd">sf audit --egress</span>`,
              `<span class="ok">offline-capable</span> — nothing in this definition can reach the network.`,
            ],
            { cue: 1, delay: 0.1, size: "sm" },
          )}
        </div>
      </div>`,
    },

    // -------------------------------------------------------------- COMPLETE
    {
      id: "close",
      vo: [
        { who: "a", text: "Software factory. Open source, Apache two point zero." },
      ],
      body: `<div class="f">
        ${rail("COMPLETE")}
        <div class="col">
          <div class="mark reveal"><span class="dot"></span><span class="wm"><b>software</b>factory</span></div>
          <div class="hair reveal" data-delay="0.3" data-grow></div>
          <div class="foot reveal" data-delay="0.5">Apache-2.0 · local-first · every definition is a file you can review</div>
        </div>
      </div>`,
    },
  ],
};
